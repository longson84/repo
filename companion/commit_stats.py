"""Repository-level Git history collected once across tracked workspaces."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping, Optional, Sequence


LOG_FORMAT = "%x1e%H%x1f%cI%x1f%an%x1f%ae%x1f%s"


def _run_git(path: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", path, *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    ).stdout


def _stamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_commit_numstat(text: str) -> list[dict[str, Any]]:
    commits = []
    for raw_record in (text or "").split("\x1e"):
        record = raw_record.lstrip("\r\n")
        if not record:
            continue
        header, _, body = record.partition("\n")
        fields = header.rstrip("\r").split("\x1f", 4)
        if len(fields) != 5 or not fields[0]:
            continue
        additions = 0
        deletions = 0
        for raw_line in body.splitlines():
            parts = raw_line.split("\t", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                additions += int(parts[0])
                deletions += int(parts[1])
        commits.append(
            {
                "sha": fields[0],
                "iso": fields[1],
                "author_name": fields[2],
                "author_email": fields[3],
                "subject": fields[4],
                "additions": additions,
                "deletions": deletions,
                "net": additions - deletions,
            }
        )
    return commits


def collect_workspace_tip(
    path: str, *, git_runner: Callable[..., str] = _run_git
) -> Optional[str]:
    try:
        value = git_runner(path, "rev-parse", "HEAD").strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return value or None


def _record_order(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(record.get("iso") or ""),
        str(record.get("author_name") or ""),
        str(record.get("author_email") or ""),
        str(record.get("subject") or ""),
        int(record.get("additions") or 0),
        int(record.get("deletions") or 0),
    )


def collect_repository_commits(
    workspace_paths: Sequence[str],
    *,
    git_runner: Callable[..., str] = _run_git,
    warnings: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Union each tracked HEAD history by SHA without assuming shared local refs."""
    by_sha: dict[str, dict[str, Any]] = {}
    warning_rows = warnings if warnings is not None else []
    for path in sorted({str(item) for item in workspace_paths}):
        try:
            text = git_runner(
                path,
                "log",
                "--numstat",
                f"--format={LOG_FORMAT}",
                "HEAD",
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            warning_rows.append(
                {"code": "workspace_history_unavailable", "workspace_id": path}
            )
            continue
        for record in parse_commit_numstat(text):
            sha = record["sha"]
            previous = by_sha.get(sha)
            if previous is not None and previous != record:
                warning_rows.append(
                    {
                        "code": "commit_metadata_conflict",
                        "workspace_id": path,
                        "commit_sha": sha,
                    }
                )
                by_sha[sha] = min((previous, record), key=_record_order)
            elif previous is None:
                by_sha[sha] = record
    return sorted(
        by_sha.values(),
        key=lambda item: (str(item.get("iso") or ""), item["sha"]),
    )


def _period_series(
    commits: Sequence[Mapping[str, Any]], now: datetime
) -> dict[str, Any]:
    by_day = Counter()
    by_week = Counter()
    by_month = Counter()
    by_year = Counter()
    for commit in commits:
        parsed = _stamp(commit.get("iso"))
        if parsed is None:
            continue
        day = parsed.date()
        by_day[day.isoformat()] += 1
        iso = day.isocalendar()
        by_week[f"{iso.year}-W{iso.week:02d}"] += 1
        by_month[f"{day.year:04d}-{day.month:02d}"] += 1
        by_year[str(day.year)] += 1

    today = now.date()
    day_keys = [
        (today - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)
    ]
    monday = today - timedelta(days=today.weekday())
    week_keys = []
    for offset in range(25, -1, -1):
        day = monday - timedelta(weeks=offset)
        iso = day.isocalendar()
        week_keys.append(f"{iso.year}-W{iso.week:02d}")
    month_keys = []
    year, month = today.year, today.month
    for _ in range(24):
        month_keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    month_keys.reverse()
    if by_year:
        year_keys = [
            str(value)
            for value in range(min(map(int, by_year)), max(map(int, by_year)) + 1)
        ]
    else:
        year_keys = [str(today.year)]

    def rows(counter: Counter, keys: Sequence[str]) -> list[dict[str, Any]]:
        return [{"period": key, "commits": counter.get(key, 0)} for key in keys]

    return {
        "commitsByDay": rows(by_day, day_keys),
        "commitsByWeek": rows(by_week, week_keys),
        "commitsByMonth": rows(by_month, month_keys),
        "commitsByYear": rows(by_year, year_keys),
        "commitsByDate": dict(by_day),
    }


def aggregate_repository_history(
    repository_id: str,
    commits: Sequence[Mapping[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    ordered = sorted(
        commits,
        key=lambda item: (str(item.get("iso") or ""), str(item.get("sha") or "")),
    )
    parsed = [(item, _stamp(item.get("iso"))) for item in ordered]
    dated = [(item, stamp) for item, stamp in parsed if stamp is not None]
    first = dated[0][0] if dated else None
    last = dated[-1][0] if dated else None
    windows = {7: 0, 30: 0, 90: 0}
    line_windows = {
        days: {"additions": 0, "deletions": 0, "net": 0} for days in (7, 30, 90)
    }
    lines_total = {"additions": 0, "deletions": 0, "net": 0}
    lines_by_date: dict[str, dict[str, int]] = {}
    contributors = Counter()
    for item, stamp in dated:
        contributors[str(item.get("author_name") or "Unknown")] += 1
        additions = int(item.get("additions") or 0)
        deletions = int(item.get("deletions") or 0)
        for bucket in (
            lines_total,
            lines_by_date.setdefault(
                stamp.date().isoformat(), {"additions": 0, "deletions": 0, "net": 0}
            ),
        ):
            bucket["additions"] += additions
            bucket["deletions"] += deletions
            bucket["net"] += additions - deletions
        age = now - stamp
        for days in windows:
            if age <= timedelta(days=days):
                windows[days] += 1
                bucket = line_windows[days]
                bucket["additions"] += additions
                bucket["deletions"] += deletions
                bucket["net"] += additions - deletions

    series = _period_series(ordered, now)
    week_counts = [row["commits"] for row in series["commitsByWeek"]]
    day_counts = [row["commits"] for row in series["commitsByDay"]]
    active_weeks = sum(value > 0 for value in week_counts)
    average_week = sum(week_counts) / len(week_counts) if week_counts else 0.0
    first_stamp = dated[0][1] if dated else None
    last_stamp = dated[-1][1] if dated else None
    age_days = (now.date() - first_stamp.date()).days if first_stamp else None
    lifespan_days = (
        (last_stamp.date() - first_stamp.date()).days
        if first_stamp and last_stamp
        else None
    )
    if age_days is None:
        age_label = None
    elif age_days >= 365:
        years, remainder = divmod(age_days, 365)
        age_label = f"{years}y {remainder // 30}mo"
    elif age_days >= 30:
        age_label = f"{age_days // 30}mo"
    else:
        age_label = f"{age_days}d"
    result = {
        "repository_id": repository_id,
        "commitCount": len(ordered),
        "commitShas": [str(item.get("sha") or "") for item in ordered],
        "firstCommitAt": first.get("iso") if first else None,
        "lastCommitAt": last.get("iso") if last else None,
        "lastCommitAuthor": last.get("author_name") if last else None,
        "lastCommitSubject": last.get("subject") if last else None,
        "commits7d": windows[7],
        "commits30d": windows[30],
        "commits90d": windows[90],
        "contributorCount": len(contributors),
        "topContributors": [
            {"name": name, "commits": count}
            for name, count in contributors.most_common(5)
        ],
        "weeklyCommits": week_counts[-12:],
        "activitySummary": {
            "peakDayCommits": max(day_counts) if day_counts else 0,
            "peakWeekCommits": max(week_counts) if week_counts else 0,
            "activeWeeks": active_weeks,
            "quietWeeks": len(week_counts) - active_weeks,
            "avgWeekCommits": round(average_week, 1),
            "burstRatio": round(max(week_counts) / average_week, 2)
            if week_counts and average_week
            else None,
        },
        "linesTotal": lines_total,
        "lines7d": line_windows[7],
        "lines30d": line_windows[30],
        "lines90d": line_windows[90],
        "linesByDate": lines_by_date,
        "ageDays": max(0, age_days) if age_days is not None else None,
        "ageLabel": age_label,
        "lifespanDays": max(0, lifespan_days) if lifespan_days is not None else None,
        **series,
    }
    return result


def choose_primary_workspace(
    workspaces: Sequence[Mapping[str, Any]], configured_path: Optional[str]
) -> Optional[str]:
    existing = sorted(
        {
            str(Path(str(item.get("path") or item.get("workspace_id"))).resolve())
            for item in workspaces
            if (item.get("path") or item.get("workspace_id"))
            and Path(str(item.get("path") or item.get("workspace_id"))).is_dir()
        }
    )
    if not existing:
        return None
    if configured_path:
        configured = str(Path(configured_path).expanduser().resolve())
        if configured in existing:
            return configured
    main_worktrees = [path for path in existing if (Path(path) / ".git").is_dir()]
    return min(main_worktrees or existing)
