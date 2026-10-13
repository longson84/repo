#!/usr/bin/env python3
"""Loopback-only repository scanner and stats collector.

Scan may fetch remotes. Pull may fast-forward ready branches.
No checkout, commit, reset, or push endpoints. Stats are read-only and never fetch.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
from urllib.parse import urlparse
import webbrowser

_COMPANION_DIR = Path(__file__).resolve().parent
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))
from ai.catalog import resolve_sources  # noqa: E402
from ai.detect import detect_all, load_detect_cache, save_detect_cache  # noqa: E402
from ai.sessions import collect_sessions, list_sessions  # noqa: E402
from ai.usage import aggregate_usage  # noqa: E402
from ai.search import allowed_transcript_paths, search_transcripts  # noqa: E402
from project_identity import (  # noqa: E402
    EMPTY_CONFIG,
    ProjectIdentityResolver,
    canonicalize_workspace_path,
    is_github_repository_id,
    load_identity_config,
    normalize_remote_url,
    prune_identity_config,
    resolve_live_repository,
    resolve_live_worktree_root,
    save_identity_config,
)
from commit_stats import (  # noqa: E402
    aggregate_repository_history,
    choose_primary_workspace,
    collect_repository_commits,
)

ORIGINS = {
    "https://repo.theana.online",
    "http://127.0.0.1:8766",
    "http://localhost:8766",
}
STATS_TTL_HOURS = 24
STATS_CACHE_VERSION = 2
SCAN_CACHE_VERSION = 2
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / ".repo-local" / "settings.json"
DEFAULT_TOKEN_FILE = REPO_ROOT / ".repo-local" / "pairing-token"
DEFAULT_DIST = REPO_ROOT / "dist"


class IdentityConfigConflict(ValueError):
    """Raised when an identity write is based on a stale configuration."""


def load_or_create_token(path, *, rotate=False):
    """Stable pairing token in .repo-local; regenerate only when rotate=True."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rotate and path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def start_ui_server(directory, port=8766):
    """Serve the dashboard static files on loopback; returns the server or None."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"UI directory not found: {directory}")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(QuietHandler, directory=str(directory))
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        print(f"Could not start local UI on 127.0.0.1:{port}: {error}", flush=True)
        return None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def git(path, *args, timeout=35):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
    )
    if result.returncode:
        raise ValueError("Git operation failed: " + args[0])
    output = result.stdout.decode("utf-8", errors="replace")
    return output if "-z" in args else output.strip()


def git_soft(path, *args, timeout=35):
    try:
        return git(path, *args, timeout=timeout)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


# Excluded from LOC: docs/config/markup/template/shell/style noise.
SCC_EXCLUDE_EXTS = (
    "json,json5,jsonc,jsonl,jsonnet,libsonnet,"
    "md,markdown,"
    "txt,text,"
    "sh,bash,bashrc,bash_login,bash_logout,bash_profile,csh,ksh,zsh,fish,nu,"
    "css,scss,sass,"
    "yaml,yml,"
    "csv,"
    "xml,xsd,dtd,fxml,mxml,"
    "ftl,"
    "svg,"
    "license,licence,copying,copying3,unlicense,unlicence,"
    "license-apache,licence-apache,license-mit,licence-mit,copyright,"
    "rtf,"
    "dot,gv,"
    "dockerfile,"
    "ini,"
    "toml,"
    "mako,mao,"
    "srt,"
    "rst,"
    "xslt,xsl,"
    "smk,snakefile"
    # Note: Qt Translation Source / TypeScript both use .ts — exclude Qt by language name only.
    # Autoconf (.in) and Systemd unit suffixes are language-name-only (too easy to over-exclude).
)
SCC_EXCLUDE_LANGUAGES = {
    "JSON",
    "JSON5",
    "JSONC",
    "JSONL",
    "Jsonnet",
    "Markdown",
    "Plain Text",
    "Shell",
    "BASH",
    "C Shell",
    "Korn Shell",
    "Nushell",
    "CSS",
    "Sass",
    "YAML",
    "CloudFormation (JSON)",
    "CloudFormation (YAML)",
    "Bitbucket Pipeline",
    "CSV",
    "XML",
    "XML Schema",
    "Document Type Definition",
    "FXML",
    "Macromedia eXtensible Markup Language",
    "Freemarker Template",
    "SVG",
    "License",
    "Rich Text Format",
    "DOT",
    "Qt Translation Source",
    "Dockerfile",
    "Docker ignore",
    "INI",
    "TOML",
    "Mako",
    "SRecode Template",
    "ReStructuredText",
    "Extensible Stylesheet Language Transformations",
    "Systemd",
    "Autoconf",
    "Snakemake",
    "TypeScript Typings",
}


def parse_scc_json(payload):
    """Aggregate scc --format json language rows into LOC totals."""
    rows = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(rows, list):
        raise ValueError("scc JSON must be a list")
    code = comment = blank = lines = files = complexity = 0
    by_language = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("Name") or "Unknown"
        if name in SCC_EXCLUDE_LANGUAGES:
            continue
        lang_code = int(row.get("Code") or 0)
        lang_comment = int(row.get("Comment") or 0)
        lang_blank = int(row.get("Blank") or 0)
        lang_lines = int(row.get("Lines") or (lang_code + lang_comment + lang_blank))
        lang_files = int(row.get("Count") or 0)
        lang_complexity = int(row.get("Complexity") or 0)
        code += lang_code
        comment += lang_comment
        blank += lang_blank
        lines += lang_lines
        files += lang_files
        complexity += lang_complexity
        by_language.append(
            {
                "name": name,
                "code": lang_code,
                "comment": lang_comment,
                "blank": lang_blank,
                "lines": lang_lines,
                "files": lang_files,
                "complexity": lang_complexity,
            }
        )
    by_language.sort(key=lambda item: item["code"], reverse=True)
    return {
        "locCode": code,
        "locComment": comment,
        "locBlank": blank,
        "locLines": lines,
        "locFiles": files,
        "locComplexity": complexity,
        "locByLanguage": by_language[:20],
    }


def collect_scc_loc(path, timeout=120):
    """Current-tree LOC via scc (code / comment / blank), excluding docs/config noise."""
    try:
        result = subprocess.run(
            [
                "scc",
                "--format",
                "json",
                "--exclude-ext",
                SCC_EXCLUDE_EXTS,
                str(path),
            ],
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as error:
        raise ValueError("scc is not installed or not on PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("scc timed out.") from error
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(message or "scc failed.")
    return parse_scc_json(result.stdout.decode("utf-8", errors="replace"))


def _series(counter, keys):
    return [{"period": key, "commits": counter.get(key, 0)} for key in keys]


def _parse_commit_stamp(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def empty_line_totals():
    return {"additions": 0, "deletions": 0, "net": 0}


def add_line_totals(bucket, additions, deletions):
    bucket["additions"] += additions
    bucket["deletions"] += deletions
    bucket["net"] += additions - deletions


def parse_numstat_log(text):
    """Parse `git log --numstat --pretty=format:%cI` into per-commit line churn."""
    commits = []
    current_iso = None
    additions = 0
    deletions = 0

    def flush():
        nonlocal current_iso, additions, deletions
        if current_iso is None:
            return
        commits.append(
            {
                "iso": current_iso,
                "additions": additions,
                "deletions": deletions,
                "net": additions - deletions,
            }
        )
        current_iso = None
        additions = 0
        deletions = 0

    for raw in (text or "").splitlines():
        line = raw.strip("\r")
        if not line:
            continue
        if "\t" in line:
            left, mid, _right = line.split("\t", 2)
            if left.isdigit() and mid.isdigit():
                additions += int(left)
                deletions += int(mid)
            continue
        # Commit header (ISO date). Flush the previous commit first.
        if current_iso is not None:
            flush()
        current_iso = line
        additions = 0
        deletions = 0
    flush()
    return commits


def line_churn_summary(commits, now=None):
    """Aggregate per-commit line churn into totals, windows, and by-date map."""
    now = now or datetime.now(timezone.utc)
    total = empty_line_totals()
    windows = {
        7: empty_line_totals(),
        30: empty_line_totals(),
        90: empty_line_totals(),
    }
    by_date = {}
    for item in commits:
        stamp = _parse_commit_stamp(item.get("iso"))
        if stamp is None:
            continue
        additions = int(item.get("additions") or 0)
        deletions = int(item.get("deletions") or 0)
        add_line_totals(total, additions, deletions)
        age = now - stamp
        for days, bucket in windows.items():
            if age <= timedelta(days=days):
                add_line_totals(bucket, additions, deletions)
        day_key = stamp.date().isoformat()
        day_bucket = by_date.setdefault(day_key, empty_line_totals())
        add_line_totals(day_bucket, additions, deletions)
    return {
        "linesTotal": total,
        "lines7d": windows[7],
        "lines30d": windows[30],
        "lines90d": windows[90],
        "linesByDate": by_date,
    }


def commit_series(iso_dates, now=None):
    """Bucket commit timestamps into day/week/month/year series."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    by_day = Counter()
    by_week = Counter()
    by_month = Counter()
    by_year = Counter()
    for value in iso_dates:
        stamp = _parse_commit_stamp(value)
        if stamp is None:
            continue
        day = stamp.date()
        by_day[day.isoformat()] += 1
        year, week, _ = day.isocalendar()
        by_week[f"{year}-W{week:02d}"] += 1
        by_month[day.strftime("%Y-%m")] += 1
        by_year[str(day.year)] += 1

    day_keys = [
        (today - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)
    ]
    monday = today - timedelta(days=today.weekday())
    week_keys = []
    for offset in range(25, -1, -1):
        week_start = monday - timedelta(weeks=offset)
        year, week, _ = week_start.isocalendar()
        week_keys.append(f"{year}-W{week:02d}")
    month_keys = []
    year_num, month_num = today.year, today.month
    for _ in range(24):
        month_keys.append(f"{year_num:04d}-{month_num:02d}")
        month_num -= 1
        if month_num == 0:
            month_num = 12
            year_num -= 1
    month_keys.reverse()
    if by_year:
        first_year = min(int(year) for year in by_year)
        last_year = max(int(year) for year in by_year)
        year_keys = [str(year) for year in range(first_year, last_year + 1)]
    else:
        year_keys = [str(today.year)]
    return {
        "commitsByDay": _series(by_day, day_keys),
        "commitsByWeek": _series(by_week, week_keys),
        "commitsByMonth": _series(by_month, month_keys),
        "commitsByYear": _series(by_year, year_keys),
        "commitsByDate": dict(by_day),
    }


def activity_summary(series):
    """Bursts vs quiet periods from day/week commit series."""
    days = series.get("commitsByDay") or []
    weeks = series.get("commitsByWeek") or []
    day_counts = [item.get("commits") or 0 for item in days]
    week_counts = [item.get("commits") or 0 for item in weeks]
    peak_day = max(day_counts) if day_counts else 0
    peak_week = max(week_counts) if week_counts else 0
    peak_day_period = next(
        (item["period"] for item in days if (item.get("commits") or 0) == peak_day),
        None,
    )
    peak_week_period = next(
        (item["period"] for item in weeks if (item.get("commits") or 0) == peak_week),
        None,
    )
    active_weeks = sum(1 for count in week_counts if count > 0)
    quiet_weeks = sum(1 for count in week_counts if count == 0)
    avg_week = (sum(week_counts) / len(week_counts)) if week_counts else 0.0
    burst_ratio = round(peak_week / avg_week, 2) if avg_week else None
    return {
        "peakDayCommits": peak_day,
        "peakDayPeriod": peak_day_period,
        "peakWeekCommits": peak_week,
        "peakWeekPeriod": peak_week_period,
        "activeWeeks": active_weeks,
        "quietWeeks": quiet_weeks,
        "avgWeekCommits": round(avg_week, 1),
        "burstRatio": burst_ratio,
    }


def age_stats(first_iso, last_iso, now=None):
    now = now or datetime.now(timezone.utc)

    def parse(value):
        if not value:
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)

    first = parse(first_iso)
    last = parse(last_iso) or now
    if not first:
        return {"ageDays": None, "ageLabel": None, "lifespanDays": None}
    age_days = max(0, (now.date() - first.date()).days)
    lifespan_days = max(0, (last.date() - first.date()).days)
    years, rem = divmod(age_days, 365)
    months = rem // 30
    if years:
        label = f"{years}y {months}mo"
    elif months:
        label = f"{months}mo"
    else:
        label = f"{age_days}d"
    return {
        "ageDays": age_days,
        "ageLabel": label,
        "lifespanDays": lifespan_days,
    }


def collect_hotspots(path):
    text = git_soft(
        path,
        "log",
        "--name-only",
        "--pretty=format:",
        "--since=18.months",
        "HEAD",
        timeout=120,
    )
    if text is None:
        text = git_soft(
            path,
            "log",
            "--name-only",
            "--pretty=format:",
            "-n",
            "3000",
            "HEAD",
            timeout=120,
        )
    if text is None:
        return None
    files = Counter()
    dirs = Counter()
    for line in text.splitlines():
        relative = line.strip()
        if not relative:
            continue
        files[relative] += 1
        dirs[relative.split("/", 1)[0]] += 1
    return {
        "hotspotFiles": [
            {"path": name, "commits": count} for name, count in files.most_common(15)
        ],
        "hotspotDirs": [
            {"name": name, "commits": count} for name, count in dirs.most_common(10)
        ],
    }


def collect_large_blobs(path):
    """Largest files in HEAD plus largest blob objects in pack history."""
    items = []
    tree = git_soft(path, "ls-tree", "-r", "-l", "HEAD", timeout=90)
    if tree is not None:
        for line in tree.splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            meta, relative = parts
            bits = meta.split()
            if len(bits) < 4 or bits[1] != "blob":
                continue
            try:
                size = int(bits[3])
            except ValueError:
                continue
            items.append(
                {
                    "path": relative,
                    "bytes": size,
                    "sha": bits[2][:12],
                    "source": "HEAD",
                }
            )

    sha_paths = {}
    objects = git_soft(path, "rev-list", "--objects", "--all", timeout=120)
    if objects is not None:
        for line in objects.splitlines():
            bits = line.split(" ", 1)
            if len(bits) == 2 and bits[1]:
                sha_paths[bits[0]] = bits[1]

    pack_dir = Path(path) / ".git" / "objects" / "pack"
    pack_sizes = {}
    if pack_dir.is_dir():
        for index in pack_dir.glob("*.idx"):
            verified = git_soft(path, "verify-pack", "-v", str(index), timeout=90)
            if not verified:
                continue
            for line in verified.splitlines():
                bits = line.split()
                if len(bits) >= 3 and bits[1] == "blob":
                    try:
                        pack_sizes[bits[0]] = int(bits[2])
                    except ValueError:
                        pass

    for sha, size in sorted(pack_sizes.items(), key=lambda item: item[1], reverse=True)[
        :40
    ]:
        relative = sha_paths.get(sha)
        items.append(
            {
                "path": relative or sha[:12],
                "bytes": size,
                "sha": sha[:12],
                "source": "history",
            }
        )

    # Prefer largest unique paths; keep HEAD and history distinct when same path.
    items.sort(key=lambda item: item["bytes"], reverse=True)
    seen = set()
    unique = []
    for item in items:
        key = (item["source"], item["path"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= 15:
            break
    return unique


def authorized(host, origin, authorization, token, port):
    return (
        host in {f"127.0.0.1:{port}", f"localhost:{port}"}
        and origin in ORIGINS
        and hmac.compare_digest(authorization or "", "Bearer " + token)
    )


def inspect_repo(path, fetch=True):
    result = dict(
        path=path,
        name=Path(path).name,
        branch=None,
        upstream=None,
        ahead=None,
        behind=None,
        modified=0,
        untracked=0,
        fresh=False,
        error=None,
        github=None,
    )
    if fetch:
        try:
            git(path, "fetch", "--all", "--prune", timeout=120)
            result["fresh"] = True
        except (ValueError, subprocess.TimeoutExpired, OSError):
            result["fresh"] = False
    try:
        result["branch"] = git(path, "symbolic-ref", "--short", "-q", "HEAD")
    except ValueError:
        result["branch"] = "Detached HEAD"
    try:
        result["upstream"] = git(
            path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        )
    except ValueError:
        pass
    try:
        entries = git(
            path, "status", "--porcelain=v1", "--untracked-files=all", "-z"
        ).split("\0")
        index = 0
        while index < len(entries):
            entry = entries[index]
            if entry:
                if entry[:2] == "??":
                    result["untracked"] += 1
                else:
                    result["modified"] += 1
                if "R" in entry[:2] or "C" in entry[:2]:
                    index += 1
            index += 1
        if not result["upstream"]:
            result["error"] = "No upstream branch. Set one in Git to compare commits."
            return result
        remote = git(path, "config", f"branch.{result['branch']}.remote")
        if remote == ".":
            result["error"] = (
                "Upstream is local; choose a remote upstream to compare machines."
            )
            return result
        url = git(path, "remote", "get-url", remote)
        if url.startswith("git@github.com:"):
            slug = url[len("git@github.com:") :]
        else:
            parsed = urlparse(url)
            slug = parsed.path.lstrip("/") if parsed.hostname == "github.com" else ""
        if slug and len(slug.rstrip("/").removesuffix(".git").split("/")) == 2:
            result["github"] = "https://github.com/" + slug.rstrip("/").removesuffix(
                ".git"
            )
        ahead, behind = git(
            path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"
        ).split()
        result.update(ahead=int(ahead), behind=int(behind))
    except (ValueError, subprocess.TimeoutExpired, OSError):
        result["error"] = (
            "Local Git check failed. Fetch in your terminal if remote-tracking refs are missing."
        )
        result.update(ahead=None, behind=None)
    return result


def ready_to_pull(result):
    return (
        not result.get("error")
        and result.get("upstream")
        and result.get("branch") not in (None, "Detached HEAD")
        and result.get("ahead") == 0
        and isinstance(result.get("behind"), int)
        and result["behind"] > 0
        and result.get("modified", 0) + result.get("untracked", 0) == 0
    )


def pull_ready_repo(path, previous=None):
    current = inspect_repo(path, fetch=False)
    if previous and not current.get("error"):
        current["fresh"] = bool(previous.get("fresh"))
    if not ready_to_pull(current):
        current["pulled"] = False
        return current
    try:
        git(path, "pull", "--ff-only", timeout=120)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        current["error"] = "Fast-forward pull failed."
        current["fresh"] = False
        current["pulled"] = False
        return current
    updated = inspect_repo(path, fetch=False)
    updated["fresh"] = True
    updated["pulled"] = True
    return updated


def tracked_pull_paths(state, requested_path=None):
    tracked = [str(state.checked(path)) for path in state.paths]
    if requested_path in (None, ""):
        return tracked
    target = str(state.checked(requested_path))
    if target not in tracked:
        raise ValueError("Repository is not tracked.")
    return [target]


def merge_scan_repos(previous, updates, tracked_paths, decorate):
    by_path = {}
    for item in (previous or {}).values():
        path = str(item.get("path") or "")
        if path:
            by_path[path] = dict(item)
    pulled = 0
    for item in updates:
        row = dict(item)
        if row.pop("pulled", False):
            pulled += 1
        path = str(row.get("path") or "")
        if path:
            by_path[path] = row
    return (
        [decorate(by_path[path]) for path in tracked_paths if path in by_path],
        pulled,
    )


def collect_stats(path):
    """Read-only local repository statistics. Never fetches remotes."""
    path = str(path)
    now = datetime.now(timezone.utc)
    result = dict(
        path=path,
        name=Path(path).name,
        head=None,
        computedAt=now.isoformat(),
        ttlHours=STATS_TTL_HOURS,
        commitCount=None,
        firstCommitAt=None,
        lastCommitAt=None,
        lastCommitAuthor=None,
        lastCommitSubject=None,
        commits7d=None,
        commits30d=None,
        commits90d=None,
        weeklyCommits=None,
        contributorCount=None,
        topContributors=None,
        branchCount=None,
        stashCount=None,
        remotes=None,
        workingTreeBytes=None,
        languages=None,
        largestDirs=None,
        filesChanged30d=None,
        commitsByDay=None,
        commitsByWeek=None,
        commitsByMonth=None,
        commitsByYear=None,
        commitsByDate=None,
        activitySummary=None,
        linesTotal=None,
        lines7d=None,
        lines30d=None,
        lines90d=None,
        linesByDate=None,
        locCode=None,
        locComment=None,
        locBlank=None,
        locLines=None,
        locFiles=None,
        locComplexity=None,
        locByLanguage=None,
        ageDays=None,
        ageLabel=None,
        lifespanDays=None,
        hotspotFiles=None,
        hotspotDirs=None,
        largeBlobs=None,
        error=None,
    )
    errors = []

    def note(message):
        errors.append(message)

    head = git_soft(path, "rev-parse", "--short", "HEAD")
    if head is None:
        result["error"] = "Could not read Git HEAD for this repository."
        return result
    result["head"] = head

    count = git_soft(path, "rev-list", "--count", "HEAD")
    if count is not None and count.isdigit():
        result["commitCount"] = int(count)
    else:
        note("commit count")

    last = git_soft(path, "log", "-1", "--format=%cI%n%an%n%s")
    if last:
        lines = last.split("\n", 2)
        if len(lines) >= 3:
            result["lastCommitAt"] = lines[0]
            result["lastCommitAuthor"] = lines[1]
            result["lastCommitSubject"] = lines[2]
        else:
            note("last commit")
    else:
        note("last commit")

    roots = git_soft(path, "rev-list", "--max-parents=0", "HEAD")
    if roots:
        first_sha = roots.splitlines()[0]
        first = git_soft(path, "log", "-1", "--format=%cI", first_sha)
        if first:
            result["firstCommitAt"] = first
        else:
            note("first commit")
    else:
        note("first commit")

    c7 = git_soft(path, "rev-list", "--count", "--since=7.days", "HEAD")
    c30 = git_soft(path, "rev-list", "--count", "--since=30.days", "HEAD")
    c90 = git_soft(path, "rev-list", "--count", "--since=90.days", "HEAD")
    if c7 is not None and c7.isdigit():
        result["commits7d"] = int(c7)
    else:
        note("commits 7d")
    if c30 is not None and c30.isdigit():
        result["commits30d"] = int(c30)
    else:
        note("commits 30d")
    if c90 is not None and c90.isdigit():
        result["commits90d"] = int(c90)
    else:
        note("commits 90d")

    numstat_log = git_soft(
        path, "log", "--numstat", "--pretty=format:%cI", "HEAD", timeout=180
    )
    if numstat_log is not None:
        commits = parse_numstat_log(numstat_log)
        series = commit_series([item["iso"] for item in commits], now=now)
        result.update(series)
        week_map = {item["period"]: item["commits"] for item in series["commitsByWeek"]}
        result["weeklyCommits"] = [
            week_map.get(item["period"], 0) for item in series["commitsByWeek"][-12:]
        ]
        result["activitySummary"] = activity_summary(series)
        result.update(line_churn_summary(commits, now=now))
    else:
        note("commit timeline")
        note("weekly commits")
        note("activity summary")
        note("line churn")

    age = age_stats(result.get("firstCommitAt"), result.get("lastCommitAt"), now=now)
    result.update(age)

    shortlog = git_soft(path, "shortlog", "-sn", "HEAD", timeout=60)
    if shortlog is not None:
        contributors = []
        for line in shortlog.splitlines():
            parts = line.strip().split("\t", 1)
            if len(parts) == 2 and parts[0].strip().isdigit():
                contributors.append(
                    {"name": parts[1], "commits": int(parts[0].strip())}
                )
        result["contributorCount"] = len(contributors)
        result["topContributors"] = contributors[:5]
    else:
        note("contributors")

    branches = git_soft(path, "branch", "--list", "--format=%(refname:short)")
    if branches is not None:
        result["branchCount"] = len([b for b in branches.splitlines() if b.strip()])
    else:
        note("branches")

    stashes = git_soft(path, "stash", "list")
    if stashes is not None:
        result["stashCount"] = len([s for s in stashes.splitlines() if s.strip()])
    else:
        note("stashes")

    remote_text = git_soft(path, "remote", "-v")
    if remote_text is not None:
        remotes = []
        seen = set()
        for line in remote_text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] not in seen:
                seen.add(parts[0])
                remotes.append(
                    {
                        "name": parts[0],
                        "url": normalize_remote_url(parts[1])
                        or "local-or-unrecognized",
                    }
                )
        result["remotes"] = remotes
    else:
        note("remotes")

    tracked = git_soft(path, "ls-files", "-z", timeout=60)
    if tracked is not None:
        total = 0
        languages = {}
        dirs = {}
        root = Path(path)
        for relative in tracked.split("\0"):
            if not relative:
                continue
            file_path = root / relative
            try:
                size = file_path.stat().st_size if file_path.is_file() else 0
            except OSError:
                size = 0
            total += size
            suffix = Path(relative).suffix.lower() or "(none)"
            entry = languages.setdefault(
                suffix, {"extension": suffix, "files": 0, "bytes": 0}
            )
            entry["files"] += 1
            entry["bytes"] += size
            top = relative.split("/", 1)[0]
            if top:
                dirs[top] = dirs.get(top, 0) + size
        result["workingTreeBytes"] = total
        result["languages"] = sorted(
            languages.values(), key=lambda item: item["bytes"], reverse=True
        )[:12]
        result["largestDirs"] = [
            {"name": name, "bytes": size}
            for name, size in sorted(
                dirs.items(), key=lambda item: item[1], reverse=True
            )[:5]
        ]
    else:
        note("working tree")

    changed = git_soft(
        path,
        "log",
        "--since=30.days",
        "--name-only",
        "--pretty=format:",
        "HEAD",
        timeout=60,
    )
    if changed is not None:
        result["filesChanged30d"] = len(
            {line for line in changed.splitlines() if line.strip()}
        )
    else:
        note("churn")

    hotspots = collect_hotspots(path)
    if hotspots is not None:
        result.update(hotspots)
    else:
        note("hotspots")

    try:
        result["largeBlobs"] = collect_large_blobs(path)
    except (OSError, ValueError, TypeError):
        note("large blobs")

    try:
        result.update(collect_scc_loc(path))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        note("scc loc")

    if errors:
        result["error"] = "Partial stats: could not read " + ", ".join(errors) + "."
    return result


def stats_fresh(cached, path):
    if not cached:
        return False
    for key in ("commitsByDay", "commitsByWeek", "commitsByMonth", "commitsByYear"):
        if not isinstance(cached.get(key), list):
            return False
    if not isinstance(cached.get("commitsByDate"), dict):
        return False
    if not isinstance(cached.get("activitySummary"), dict):
        return False
    if not isinstance(cached.get("linesByDate"), dict):
        return False
    for key in ("linesTotal", "lines7d", "lines30d", "lines90d"):
        value = cached.get(key)
        if (
            not isinstance(value, dict)
            or "additions" not in value
            or "deletions" not in value
        ):
            return False
    if cached.get("locCode") is None:
        return False
    if cached.get("locComplexity") is None:
        return False
    langs = cached.get("locByLanguage")
    if not isinstance(langs, list):
        return False
    for item in langs:
        if not isinstance(item, dict) or "complexity" not in item:
            return False
        if item.get("name") in SCC_EXCLUDE_LANGUAGES:
            return False
    if not isinstance(cached.get("hotspotFiles"), list):
        return False
    if not isinstance(cached.get("largeBlobs"), list):
        return False
    if cached.get("ageDays") is None and cached.get("firstCommitAt"):
        return False
    if cached.get("commits7d") is None and cached.get("commitCount") is not None:
        return False
    head = git_soft(path, "rev-parse", "--short", "HEAD")
    if not head or cached.get("head") != head:
        return False
    try:
        computed = datetime.fromisoformat(cached["computedAt"])
    except (KeyError, TypeError, ValueError):
        return False
    if computed.tzinfo is None:
        computed = computed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - computed
    return age.total_seconds() < STATS_TTL_HOURS * 3600


def _sum_period_series(repositories, field):
    totals = Counter()
    for repository in repositories:
        for row in repository.get(field) or []:
            if isinstance(row, dict) and row.get("period") is not None:
                totals[str(row["period"])] += int(row.get("commits") or 0)
    return [{"period": period, "commits": totals[period]} for period in sorted(totals)]


def _aggregate_project_stats(project_id, project_name, repositories):
    count_fields = ("commitCount", "commits7d", "commits30d", "commits90d")
    loc_fields = (
        "workingTreeBytes",
        "locCode",
        "locComment",
        "locBlank",
        "locLines",
        "locFiles",
        "locComplexity",
    )
    line_fields = ("linesTotal", "lines7d", "lines30d", "lines90d")
    result = {
        "project_id": project_id,
        "project_name": project_name,
        "name": project_name,
        "repositories": repositories,
        "repository_count": len(repositories),
        "workspace_count": sum(
            len(item.get("workspaces") or []) for item in repositories
        ),
        "identity_warnings": [
            warning
            for repository in repositories
            for warning in (repository.get("identity_warnings") or [])
        ],
    }
    for field in count_fields:
        values = [item.get(field) for item in repositories]
        result[field] = sum(int(value or 0) for value in values) if values else 0
    for field in loc_fields:
        values = [
            item.get(field) for item in repositories if item.get(field) is not None
        ]
        result[field] = sum(int(value) for value in values) if values else None
    for field in line_fields:
        bucket = {"additions": 0, "deletions": 0, "net": 0}
        for repository in repositories:
            value = repository.get(field) or {}
            for key in bucket:
                bucket[key] += int(value.get(key) or 0)
        result[field] = bucket
    commits_by_date = Counter()
    lines_by_date = {}
    contributors = Counter()
    for repository in repositories:
        commits_by_date.update(repository.get("commitsByDate") or {})
        for day, value in (repository.get("linesByDate") or {}).items():
            bucket = lines_by_date.setdefault(
                day, {"additions": 0, "deletions": 0, "net": 0}
            )
            for key in bucket:
                bucket[key] += int(value.get(key) or 0)
        for contributor in repository.get("topContributors") or []:
            contributors[str(contributor.get("name") or "Unknown")] += int(
                contributor.get("commits") or 0
            )
    result["commitsByDate"] = dict(commits_by_date)
    result["linesByDate"] = lines_by_date
    result["contributorCount"] = len(contributors)
    result["topContributors"] = [
        {"name": name, "commits": count} for name, count in contributors.most_common(5)
    ]
    for field in ("commitsByDay", "commitsByWeek", "commitsByMonth", "commitsByYear"):
        result[field] = _sum_period_series(repositories, field)
    result["weeklyCommits"] = [row["commits"] for row in result["commitsByWeek"][-12:]]
    first_values = [
        item.get("firstCommitAt") for item in repositories if item.get("firstCommitAt")
    ]
    last_values = [
        item.get("lastCommitAt") for item in repositories if item.get("lastCommitAt")
    ]
    result["firstCommitAt"] = min(first_values) if first_values else None
    result["lastCommitAt"] = max(last_values) if last_values else None
    return result


class State:
    RECONCILIATION_COUNT_KEYS = (
        "removed_paths",
        "removed_repository_memberships",
        "removed_primary_workspaces",
        "removed_workspace_aliases",
        "removed_projects",
    )

    def __init__(self, roots, config):
        self.roots = [Path(p).expanduser().resolve(strict=True) for p in roots]
        self.config = Path(config)
        self.stats_path = self.config.parent / "stats.json"
        self.scan_path = self.config.parent / "scan.json"
        self.identity_path = self.config.parent / "project-identities.json"
        self.ai_detect_path = self.config.parent / "ai" / "detect.json"
        self.ai_usage_cache_path = self.config.parent / "ai" / "usage.db"
        self.lock = threading.Lock()
        self.ai_cache_lock = threading.Lock()
        self.paths = []
        self.stats_cache = {"version": STATS_CACHE_VERSION, "repos": {}}
        self.scan_cache = {
            "version": SCAN_CACHE_VERSION,
            "scannedAt": None,
            "repos": {},
        }
        self.identity_config = dict(EMPTY_CONFIG)
        self.identity_resolver = ProjectIdentityResolver(self.identity_config)
        self.identity_config_warnings = []
        self.ai_detect_cache = None
        self._selected_repository_ids = set()
        self.last_reconciliation = {
            **{key: 0 for key in self.RECONCILIATION_COUNT_KEYS},
            "invalid_identity_config": False,
        }
        if self.config.exists():
            raw = json.loads(self.config.read_text())
            raw_paths = raw.get("paths", [])
            self.paths = list(raw_paths) if isinstance(raw_paths, list) else []
        self.reload_identity_config()
        self.load_stats_cache()
        self.load_scan_cache()
        self.load_ai_detect_cache()
        self.reconcile_tracking()

    def reload_identity_config(self):
        try:
            config = load_identity_config(self.identity_path)
            warnings = []
        except ValueError:
            config = dict(EMPTY_CONFIG)
            warnings = [{"code": "invalid_identity_config"}]
        self.identity_config = config
        self.identity_resolver = ProjectIdentityResolver(config)
        self.identity_config_warnings = warnings
        return config

    def invalidate_identity_caches(self):
        self.stats_cache = {"version": STATS_CACHE_VERSION, "repos": {}}
        self.scan_cache = {
            "version": SCAN_CACHE_VERSION,
            "scannedAt": None,
            "repos": {},
        }
        self.save_stats_cache()
        self.save_scan_cache()

    def install_identity_config(self, config, warnings=None, *, invalidate=False):
        self.identity_config = config
        self.identity_resolver = ProjectIdentityResolver(config)
        self.identity_config_warnings = list(warnings or [])
        if invalidate:
            self.invalidate_identity_caches()

    def _candidate_path(self, value):
        path = Path(canonicalize_workspace_path(str(value)))
        if not any(path == root or root in path.parents for root in self.roots):
            raise ValueError(
                "Path is outside the folders approved when starting the companion."
            )
        return path

    def _save_selection(self):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.with_suffix(".tmp")
        temporary.write_text(json.dumps({"paths": self.paths}, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.config)

    def selected_repository_ids(self):
        return set(self._selected_repository_ids)

    def cached_repository_ids(self):
        repository_ids = set()
        for payload in self.scan_cache.get("repos", {}).values():
            if isinstance(payload, dict) and payload.get("repository_id"):
                repository_ids.add(payload["repository_id"])
        for project in self.stats_cache.get("projects", []):
            if not isinstance(project, dict):
                continue
            for repository in project.get("repositories", []):
                if isinstance(repository, dict) and repository.get("repository_id"):
                    repository_ids.add(repository["repository_id"])
        return repository_ids

    def reconcile_tracking(self, candidate_paths=None):
        previous_repository_ids = self.selected_repository_ids()
        previous_fingerprint = self.identity_resolver.fingerprint()
        previous_warnings = list(self.identity_config_warnings)
        self.reload_identity_config()
        identity_reloaded = (
            self.identity_resolver.fingerprint() != previous_fingerprint
            or self.identity_config_warnings != previous_warnings
        )
        candidates = self.paths if candidate_paths is None else candidate_paths
        candidates = list(candidates or [])
        retained_paths = []
        selected_repository_ids = set()
        for value in candidates:
            try:
                path = self._candidate_path(value)
                worktree_root = resolve_live_worktree_root(str(path))
                if worktree_root is None:
                    continue
                path = self._candidate_path(worktree_root)
                repository_id, _ = resolve_live_repository(str(path))
            except (OSError, TypeError, ValueError):
                continue
            if not is_github_repository_id(repository_id):
                continue
            path_text = str(path)
            if path_text not in retained_paths:
                retained_paths.append(path_text)
            selected_repository_ids.add(repository_id)

        selection_changed = retained_paths != self.paths
        self.paths = retained_paths
        repository_scope_changed = bool(previous_repository_ids) and (
            selected_repository_ids != previous_repository_ids
        )
        cached_repository_ids = self.cached_repository_ids()
        cached_scope_obsolete = bool(cached_repository_ids - selected_repository_ids)
        self._selected_repository_ids = selected_repository_ids
        if selection_changed:
            self._save_selection()

        invalid_identity_config = any(
            warning.get("code") == "invalid_identity_config"
            for warning in self.identity_config_warnings
        )
        identity_counts = {
            key: 0 for key in self.RECONCILIATION_COUNT_KEYS if key != "removed_paths"
        }
        identity_changed = False
        if not invalid_identity_config:
            alias_repository_ids = {}
            for project in self.identity_config.get("projects", []):
                for alias in project.get("workspace_aliases", []):
                    try:
                        repository_id, _ = resolve_live_repository(alias)
                    except (OSError, TypeError, ValueError):
                        continue
                    if repository_id in selected_repository_ids:
                        alias_repository_ids[alias] = repository_id
            pruned, identity_counts = prune_identity_config(
                self.identity_config,
                selected_repository_ids,
                alias_repository_ids,
            )
            identity_changed = pruned != self.identity_config
            if identity_changed:
                saved = save_identity_config(self.identity_path, pruned)
                self.install_identity_config(saved)

        if (
            selection_changed
            or repository_scope_changed
            or cached_scope_obsolete
            or identity_changed
            or identity_reloaded
        ):
            self.invalidate_identity_caches()
        result = {
            "removed_paths": len(candidates) - len(retained_paths),
            **identity_counts,
            "invalid_identity_config": invalid_identity_config,
        }
        if (
            candidate_paths is not None
            or invalid_identity_config
            or any(result[key] for key in self.RECONCILIATION_COUNT_KEYS)
        ):
            self.last_reconciliation = result
        return dict(result)

    def save_identity_config(self, payload, expected_fingerprint=None):
        current = load_identity_config(self.identity_path)
        current_fingerprint = ProjectIdentityResolver(current).fingerprint()
        if expected_fingerprint is not None:
            if not isinstance(expected_fingerprint, str):
                raise ValueError("expected_fingerprint must be a string")
            if expected_fingerprint != current_fingerprint:
                raise IdentityConfigConflict(
                    "Project grouping changed in another editor. Reload and try again."
                )
        saved = save_identity_config(self.identity_path, payload)
        self.install_identity_config(saved, invalidate=True)
        return saved

    def decorate_workspace(self, item):
        path = str(item.get("path") or "")
        identity = self.identity_resolver.resolve(path)
        return {
            **item,
            **identity,
            "path": identity["workspace_id"],
            "name": item.get("name") or identity["workspace_name"],
        }

    def workspace_inventory(self):
        return [
            self.decorate_workspace({"path": path, "name": Path(path).name})
            for path in self.paths
        ]

    def identity_payload(self):
        previous_fingerprint = self.identity_resolver.fingerprint()
        try:
            disk_config = load_identity_config(self.identity_path)
            disk_warnings = []
        except ValueError:
            disk_config = dict(EMPTY_CONFIG)
            disk_warnings = [{"code": "invalid_identity_config"}]
        disk_fingerprint = ProjectIdentityResolver(disk_config).fingerprint()
        self.install_identity_config(
            disk_config,
            disk_warnings,
            invalidate=disk_fingerprint != previous_fingerprint,
        )
        warnings = list(self.identity_config_warnings)
        seen = {(item.get("code"), item.get("workspace_id")) for item in warnings}
        for workspace in self.workspace_inventory():
            for code in workspace.get("identity_warnings") or []:
                key = (code, workspace["workspace_id"])
                if key not in seen:
                    warnings.append(
                        {"code": code, "workspace_id": workspace["workspace_id"]}
                    )
                    seen.add(key)
        return {
            **self.identity_config,
            "warnings": warnings,
            "fingerprint": self.identity_resolver.fingerprint(),
        }

    def load_ai_detect_cache(self):
        self.ai_detect_cache = load_detect_cache(self.ai_detect_path)

    def run_ai_detect(self):
        payload = detect_all(resolve_sources())
        save_detect_cache(self.ai_detect_path, payload)
        self.ai_detect_cache = payload
        return payload

    def list_ai_sessions(self, data):
        sources = data.get("sources")
        if sources is not None and not isinstance(sources, list):
            raise ValueError("sources must be a list of source ids.")
        if isinstance(sources, list):
            for item in sources:
                if not isinstance(item, str):
                    raise ValueError("sources must be strings.")
        project = data.get("project")
        if project is not None and not isinstance(project, str):
            raise ValueError("project must be a string.")
        limit = data.get("limit", 100)
        if not isinstance(limit, int):
            raise ValueError("limit must be an integer.")
        include_executions = data.get("include_executions", False)
        if not isinstance(include_executions, bool):
            raise ValueError("include_executions must be a boolean.")
        conversation_keys = data.get("conversation_keys")
        if conversation_keys is not None and not isinstance(conversation_keys, list):
            raise ValueError("conversation_keys must be a list of strings.")
        if isinstance(conversation_keys, list) and not all(
            isinstance(item, str) for item in conversation_keys
        ):
            raise ValueError("conversation_keys must be strings.")
        return list_sessions(
            sources=sources,
            project=project,
            limit=limit,
            include_executions=include_executions,
            conversation_keys=conversation_keys,
            identity_resolver=self.identity_resolver,
            allowed_repository_ids=self.selected_repository_ids(),
        )

    def list_ai_usage(self, data):
        sources = data.get("sources")
        if sources is not None and not isinstance(sources, list):
            raise ValueError("sources must be a list of source ids.")
        if isinstance(sources, list):
            for item in sources:
                if not isinstance(item, str):
                    raise ValueError("sources must be strings.")
        session_ids = data.get("session_ids")
        if session_ids is not None and not isinstance(session_ids, list):
            raise ValueError("session_ids must be a list of strings.")
        if isinstance(session_ids, list):
            for item in session_ids:
                if not isinstance(item, str):
                    raise ValueError("session_ids must be strings.")
        conversation_keys = data.get("conversation_keys")
        if conversation_keys is not None and not isinstance(conversation_keys, list):
            raise ValueError("conversation_keys must be a list of strings.")
        if isinstance(conversation_keys, list) and not all(
            isinstance(item, str) for item in conversation_keys
        ):
            raise ValueError("conversation_keys must be strings.")
        compound_sessions = data.get("sessions")
        if compound_sessions is not None and not isinstance(compound_sessions, list):
            raise ValueError("sessions must be a list of objects.")
        if isinstance(compound_sessions, list):
            for item in compound_sessions:
                if not isinstance(item, dict):
                    raise ValueError("sessions must contain objects.")
                if not isinstance(item.get("source_id"), str) or not isinstance(
                    item.get("session_id"), str
                ):
                    raise ValueError(
                        "sessions require string source_id and session_id."
                    )
        with self.ai_cache_lock:
            return aggregate_usage(
                sources=sources,
                session_ids=session_ids,
                conversation_keys=conversation_keys,
                compound_sessions=compound_sessions,
                cache_path=self.ai_usage_cache_path,
                identity_resolver=self.identity_resolver,
                allowed_repository_ids=self.selected_repository_ids(),
            )

    def run_ai_search(self, data):
        q = data.get("q")
        if not isinstance(q, str):
            raise ValueError("q must be a string.")
        sources = data.get("sources")
        if sources is not None and not isinstance(sources, list):
            raise ValueError("sources must be a list of source ids.")
        if isinstance(sources, list):
            for item in sources:
                if not isinstance(item, str):
                    raise ValueError("sources must be strings.")
        limit = data.get("limit", 50)
        if not isinstance(limit, int):
            raise ValueError("limit must be an integer.")
        sessions = collect_sessions(
            sources=sources,
            identity_resolver=self.identity_resolver,
            allowed_repository_ids=self.selected_repository_ids(),
        )
        allowed_paths = allowed_transcript_paths(sessions)
        return search_transcripts(
            q,
            sources=sources,
            limit=limit,
            allowed_paths=allowed_paths,
        )

    def checked(self, value):
        path = Path(value).expanduser().resolve(strict=True)
        if not any(path == root or root in path.parents for root in self.roots):
            raise ValueError(
                "Path is outside the folders approved when starting the companion."
            )
        return path

    def discover(self, value):
        root = self.checked(value)
        found = []
        excluded_candidate_count = 0
        for directory, folders, files in os.walk(root, followlinks=False):
            path = Path(directory)
            if ".git" in folders or ".git" in files:
                repository_id, _ = resolve_live_repository(str(path.resolve()))
                if is_github_repository_id(repository_id):
                    found.append(
                        self.decorate_workspace(
                            {"name": path.name, "path": str(path.resolve())}
                        )
                    )
                else:
                    excluded_candidate_count += 1
                folders[:] = []
            else:
                folders[:] = [
                    name
                    for name in folders
                    if not name.startswith(".")
                    and name not in {"node_modules", "vendor", "dist", "build"}
                    and len(path.relative_to(root).parts) < 3
                ]
            if len(found) > 1000:
                raise ValueError("Too many repositories. Choose a smaller folder.")
        return {
            "repositories": sorted(found, key=lambda row: row["path"]),
            "excluded_candidate_count": excluded_candidate_count,
        }

    def select(self, values):
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("Select at most 100 repositories.")
        candidates = [str(self._candidate_path(value)) for value in values]
        return self.reconcile_tracking(candidates)

    def load_stats_cache(self):
        if not self.stats_path.exists():
            self.stats_cache = {"version": STATS_CACHE_VERSION, "repos": {}}
            return
        try:
            raw = json.loads(self.stats_path.read_text())
            if not isinstance(raw, dict) or raw.get("version") != STATS_CACHE_VERSION:
                self.stats_cache = {"version": STATS_CACHE_VERSION, "repos": {}}
                return
            repos = raw.get("repos", {}) if isinstance(raw, dict) else {}
            self.stats_cache = {
                "version": STATS_CACHE_VERSION,
                "repos": repos if isinstance(repos, dict) else {},
            }
            projects = raw.get("projects")
            if isinstance(projects, list):
                self.stats_cache["projects"] = projects
        except (OSError, json.JSONDecodeError, TypeError):
            self.stats_cache = {"version": STATS_CACHE_VERSION, "repos": {}}

    def save_stats_cache(self):
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.stats_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.stats_cache, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.stats_path)

    def load_scan_cache(self):
        if not self.scan_path.exists():
            self.scan_cache = {
                "version": SCAN_CACHE_VERSION,
                "scannedAt": None,
                "repos": {},
            }
            return
        try:
            raw = json.loads(self.scan_path.read_text())
            if not isinstance(raw, dict) or raw.get("version") != SCAN_CACHE_VERSION:
                self.scan_cache = {
                    "version": SCAN_CACHE_VERSION,
                    "scannedAt": None,
                    "repos": {},
                }
                return
            repos = raw.get("repos", {}) if isinstance(raw, dict) else {}
            self.scan_cache = {
                "version": SCAN_CACHE_VERSION,
                "scannedAt": raw.get("scannedAt") if isinstance(raw, dict) else None,
                "repos": repos if isinstance(repos, dict) else {},
            }
            self.prune_scan_cache(save=False)
        except (OSError, json.JSONDecodeError, TypeError):
            self.scan_cache = {
                "version": SCAN_CACHE_VERSION,
                "scannedAt": None,
                "repos": {},
            }

    def save_scan_cache(self):
        self.scan_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.scan_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.scan_cache, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.scan_path)

    def prune_scan_cache(self, *, save=True):
        kept = {
            path: payload
            for path, payload in self.scan_cache.get("repos", {}).items()
            if path in self.paths
        }
        scanned_at = self.scan_cache.get("scannedAt") if kept else None
        next_cache = {
            "version": SCAN_CACHE_VERSION,
            "scannedAt": scanned_at,
            "repos": kept,
        }
        if next_cache != self.scan_cache:
            self.scan_cache = next_cache
            if save:
                self.save_scan_cache()

    def remember_scan(self, repositories, scanned_at):
        repos = {}
        for item in repositories or []:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            try:
                path = str(self.checked(item["path"]))
            except (ValueError, OSError):
                continue
            if path in self.paths:
                stored = self.decorate_workspace(dict(item))
                stored["path"] = path
                repos[path] = stored
        self.scan_cache = {
            "version": SCAN_CACHE_VERSION,
            "scannedAt": scanned_at,
            "repos": repos,
        }
        self.save_scan_cache()

    def last_scan_payload(self):
        repos = self.scan_cache.get("repos", {})
        return {
            "scannedAt": self.scan_cache.get("scannedAt") if repos else None,
            "repositories": [repos[path] for path in self.paths if path in repos],
        }

    def cached_stats(self):
        repos = self.stats_cache.get("repos", {})
        return [repos[path] for path in self.paths if path in repos]

    def cached_projects(self):
        projects = self.stats_cache.get("projects")
        return projects if isinstance(projects, list) else []

    def stats_for_path(self, value, refresh=False):
        path = str(self.checked(value))
        if path not in self.paths:
            raise ValueError("Repository is not in the tracked selection.")
        cached = self.stats_cache.get("repos", {}).get(path)
        if not refresh and stats_fresh(cached, path):
            return cached
        payload = collect_stats(path)
        self.stats_cache.setdefault("repos", {})[path] = payload
        self.save_stats_cache()
        return payload

    def stats_for_all(self, refresh=False):
        def one(path):
            cached = self.stats_cache.get("repos", {}).get(path)
            if not refresh and stats_fresh(cached, path):
                return path, cached, False
            return path, collect_stats(path), True

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, list(self.paths)))
        dirty = False
        payloads = []
        for path, payload, wrote in results:
            payloads.append(payload)
            if wrote:
                self.stats_cache.setdefault("repos", {})[path] = payload
                dirty = True
        if dirty:
            self.save_stats_cache()
        return payloads

    def canonical_stats(self, refresh=False):
        workspace_stats = self.stats_for_all(refresh=refresh)
        stats_by_path = {item["path"]: item for item in workspace_stats}
        inventory = self.workspace_inventory()
        scan_rows = self.scan_cache.get("repos", {})
        repository_groups = {}
        for workspace in inventory:
            repository_groups.setdefault(workspace["repository_id"], []).append(
                workspace
            )

        configured_primaries = {}
        for project in self.identity_config.get("projects") or []:
            configured_primaries.update(project.get("primary_workspaces") or {})

        repository_rows = {}
        for repository_id, workspaces in repository_groups.items():
            paths = [item["workspace_id"] for item in workspaces]
            primary_path = choose_primary_workspace(
                workspaces, configured_primaries.get(repository_id)
            )
            primary_stats = dict(stats_by_path.get(primary_path) or {})
            warnings = []
            commits = collect_repository_commits(paths, warnings=warnings)
            history = aggregate_repository_history(repository_id, commits)
            workspace_rows = []
            for workspace in workspaces:
                path = workspace["workspace_id"]
                operational = scan_rows.get(path) or inspect_repo(path, False)
                workspace_rows.append(
                    {
                        **workspace,
                        **operational,
                        "path": path,
                        "name": workspace["workspace_name"],
                        "primary": path == primary_path,
                    }
                )
            sample = workspaces[0]
            row = {
                **primary_stats,
                **history,
                "repository_id": repository_id,
                "repository_name": sample["repository_name"],
                "name": sample["repository_name"],
                "path": primary_path,
                "primary_workspace_id": primary_path,
                "workspaces": sorted(
                    workspace_rows, key=lambda item: item["workspace_id"]
                ),
                "identity_warnings": warnings,
            }
            row.pop("commitShas", None)
            repository_rows[repository_id] = row

        project_groups = {}
        for workspace in inventory:
            key = workspace["project_id"]
            group = project_groups.setdefault(
                key, {"name": workspace["project_name"], "repository_ids": []}
            )
            if workspace["repository_id"] not in group["repository_ids"]:
                group["repository_ids"].append(workspace["repository_id"])
        projects = []
        for project_id, group in project_groups.items():
            repositories = [
                repository_rows[repository_id]
                for repository_id in group["repository_ids"]
                if repository_id in repository_rows
            ]
            projects.append(
                _aggregate_project_stats(project_id, group["name"], repositories)
            )
        projects.sort(
            key=lambda item: (item["project_name"].casefold(), item["project_id"])
        )
        self.stats_cache["projects"] = projects
        self.save_stats_cache()
        return {"repositories": workspace_stats, "projects": projects}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def headers_allowed(self):
        return (
            self.headers.get("Host")
            in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            and self.headers.get("Origin") in ORIGINS
        )

    def send_json(self, status, data):
        payload = json.dumps(data).encode()
        try:
            self.send_response(status)
            if self.headers_allowed():
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except ConnectionError:
            return

    def do_OPTIONS(self):
        if not self.headers_allowed():
            return self.send_json(403, {"error": "Origin or host rejected."})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        self.handle_api()

    def do_POST(self):
        self.handle_api()

    def handle_api(self):
        if not authorized(
            self.headers.get("Host"),
            self.headers.get("Origin"),
            self.headers.get("Authorization"),
            self.server.token,
            self.server.server_port,
        ):
            return self.send_json(
                403, {"error": "Pairing token, origin, or host rejected."}
            )
        if not self.server.state.lock.acquire(blocking=False):
            return self.send_json(
                409,
                {"error": "Another operation is running. Try again when it finishes."},
            )
        try:
            data = {}
            if self.command == "POST":
                if self.headers.get("Content-Type") != "application/json":
                    raise ValueError("JSON required.")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("Invalid request size.")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object.")
            state = self.server.state
            state.reconcile_tracking()
            if self.command == "GET" and self.path == "/api/status":
                response = {
                    "machine": socket.gethostname(),
                    "roots": [str(p) for p in state.roots],
                    "paths": state.paths,
                    "workspaces": state.workspace_inventory(),
                    "reconciliation": state.last_reconciliation,
                    **state.last_scan_payload(),
                }
            elif self.command == "GET" and self.path == "/api/project-identities":
                response = state.identity_payload()
            elif self.command == "POST" and self.path == "/api/project-identities":
                state.save_identity_config(
                    {"version": data.get("version"), "projects": data.get("projects")},
                    expected_fingerprint=data.get("expected_fingerprint"),
                )
                state.reconcile_tracking()
                response = state.identity_payload()
            elif self.command == "POST" and self.path == "/api/discover":
                response = state.discover(data["root"])
            elif self.command == "POST" and self.path == "/api/selection":
                reconciliation = state.select(data["paths"])
                response = {
                    "paths": state.paths,
                    "workspaces": state.workspace_inventory(),
                    "reconciliation": reconciliation,
                    **state.last_scan_payload(),
                }
            elif self.command == "POST" and self.path == "/api/scan":
                paths = [str(state.checked(p)) for p in state.paths]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    repos = [
                        state.decorate_workspace(item)
                        for item in pool.map(inspect_repo, paths)
                    ]
                scanned_at = datetime.now(timezone.utc).isoformat()
                state.remember_scan(repos, scanned_at)
                response = {
                    "machine": socket.gethostname(),
                    "scannedAt": scanned_at,
                    "repositories": repos,
                }
            elif self.command == "POST" and self.path == "/api/pull":
                paths = tracked_pull_paths(state, data.get("path"))
                previous = state.scan_cache.get("repos") or {}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    raw = list(
                        pool.map(
                            lambda path: pull_ready_repo(path, previous.get(path)),
                            paths,
                        )
                    )
                repos, pulled = merge_scan_repos(
                    previous, raw, state.paths, state.decorate_workspace
                )
                scanned_at = datetime.now(timezone.utc).isoformat()
                state.remember_scan(repos, scanned_at)
                response = {
                    "machine": socket.gethostname(),
                    "scannedAt": scanned_at,
                    "pulled": pulled,
                    "repositories": repos,
                }
            elif self.command == "GET" and self.path == "/api/stats":
                response = {
                    "machine": socket.gethostname(),
                    "repositories": state.cached_stats(),
                    "projects": state.cached_projects(),
                }
            elif self.command == "POST" and self.path == "/api/stats":
                if data.get("all"):
                    canonical = state.canonical_stats(refresh=bool(data.get("refresh")))
                    response = {
                        "machine": socket.gethostname(),
                        **canonical,
                    }
                elif "path" in data:
                    repository = state.stats_for_path(
                        data["path"], refresh=bool(data.get("refresh"))
                    )
                    canonical = state.canonical_stats(refresh=False)
                    response = {
                        "machine": socket.gethostname(),
                        "repositories": [repository],
                        "projects": canonical["projects"],
                    }
                else:
                    raise ValueError("Provide path or all=true for stats.")
            elif self.command == "GET" and self.path == "/api/ai/detect":
                cached = state.ai_detect_cache
                if not cached:
                    response = {
                        "version": 3,
                        "detectedAt": None,
                        "sources": [],
                        "summary": {"total": 0, "stores_found": 0, "found": 0},
                    }
                else:
                    response = cached
            elif self.command == "POST" and self.path == "/api/ai/detect":
                response = state.run_ai_detect()
            elif self.command == "POST" and self.path == "/api/ai/sessions":
                response = state.list_ai_sessions(data)
            elif self.command == "POST" and self.path == "/api/ai/usage":
                response = state.list_ai_usage(data)
            elif self.command == "POST" and self.path == "/api/ai/search":
                response = state.run_ai_search(data)
            else:
                return self.send_json(404, {"error": "Unknown endpoint."})
            self.send_json(200, response)
        except IdentityConfigConflict as error:
            self.send_json(
                409,
                {"error": str(error), "code": "identity_config_conflict"},
            )
        except ConnectionError:
            return
        except (ValueError, KeyError, OSError, TypeError) as error:
            self.send_json(
                400,
                {
                    "error": str(error)
                    if isinstance(error, ValueError)
                    else "Invalid path or request. Check your selection."
                },
            )
        finally:
            self.server.state.lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        required=True,
        help="Approved discovery folder; repeat for multiple folders.",
    )
    parser.add_argument("--port", type=int, default=48731)
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
    )
    parser.add_argument(
        "--token-file",
        default=str(DEFAULT_TOKEN_FILE),
        help="File that stores the stable pairing token (default: .repo-local/pairing-token).",
    )
    parser.add_argument(
        "--new-token",
        action="store_true",
        help="Replace the saved pairing token (forces browsers to reconnect).",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Serve dist/ on the UI port, open the local dashboard, and auto-pair.",
    )
    parser.add_argument("--ui-port", type=int, default=8766)
    parser.add_argument(
        "--dist",
        default=str(DEFAULT_DIST),
        help="Dashboard directory to serve with --ui.",
    )
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        raise SystemExit(
            f"Companion port 127.0.0.1:{args.port} is already in use ({error}).\n"
            f"Stop the other process, e.g. "
            f"`lsof -nP -iTCP:{args.port} -sTCP:LISTEN` then `kill <pid>`, "
            f"or pass a free --port."
        ) from error
    server.state = State(args.root, args.config)
    server.token = load_or_create_token(args.token_file, rotate=args.new_token)
    print(f"Repo companion: http://127.0.0.1:{args.port}", flush=True)
    print(f"Pairing token: {server.token}", flush=True)
    print(f"Token file: {args.token_file}", flush=True)
    ui = None
    if args.ui:
        ui = start_ui_server(args.dist, args.ui_port)
        if ui:
            ui_url = f"http://127.0.0.1:{args.ui_port}/?pair={server.token}"
            print(f"Local dashboard: http://127.0.0.1:{args.ui_port}", flush=True)
            print("Opening the paired local dashboard…", flush=True)
            try:
                webbrowser.open(ui_url)
            except Exception as error:  # pragma: no cover - host dependent
                print(f"Open this URL manually: {ui_url} ({error})", flush=True)
        else:
            print(
                "UI server unavailable; paste the token at https://repo.theana.online "
                "or free the UI port and restart with --ui.",
                flush=True,
            )
    else:
        print(
            "Open https://repo.theana.online and paste this token, or restart with --ui "
            "for the local dashboard.",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if ui:
            ui.shutdown()
            ui.server_close()
        server.server_close()


if __name__ == "__main__":
    main()
