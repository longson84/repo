#!/usr/bin/env python3
"""Loopback-only repository scanner and stats collector.

No checkout, pull, commit, or push endpoints. Stats are read-only and never fetch.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
from urllib.parse import urlparse

ORIGINS = {
    "https://repo.theana.online",
    "http://127.0.0.1:8766",
    "http://localhost:8766",
}
STATS_TTL_HOURS = 24


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
        if fetch:
            git(path, "fetch", "--prune", remote)
        ahead, behind = git(
            path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"
        ).split()
        result.update(ahead=int(ahead), behind=int(behind), fresh=fetch)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        result["error"] = (
            "Git check or fetch failed. Check remote access in your terminal; counts are unknown."
        )
        result.update(ahead=None, behind=None, fresh=False)
    return result


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

    c30 = git_soft(path, "rev-list", "--count", "--since=30.days", "HEAD")
    c90 = git_soft(path, "rev-list", "--count", "--since=90.days", "HEAD")
    if c30 is not None and c30.isdigit():
        result["commits30d"] = int(c30)
    else:
        note("commits 30d")
    if c90 is not None and c90.isdigit():
        result["commits90d"] = int(c90)
    else:
        note("commits 90d")

    stamps = git_soft(
        path, "log", "--since=84.days", "--format=%ct", "HEAD", timeout=60
    )
    if stamps is not None:
        week = 7 * 24 * 3600
        end = int(now.timestamp())
        buckets = [0] * 12
        for line in stamps.splitlines():
            if not line.isdigit():
                continue
            age = end - int(line)
            if age < 0:
                continue
            index = min(11, age // week)
            buckets[11 - index] += 1
        result["weeklyCommits"] = buckets
    else:
        note("weekly commits")

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
                remotes.append({"name": parts[0], "url": parts[1]})
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
            entry = languages.setdefault(suffix, {"extension": suffix, "files": 0, "bytes": 0})
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
            for name, size in sorted(dirs.items(), key=lambda item: item[1], reverse=True)[:5]
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

    if errors:
        result["error"] = "Partial stats: could not read " + ", ".join(errors) + "."
    return result


def stats_fresh(cached, path):
    if not cached:
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


class State:
    def __init__(self, roots, config):
        self.roots = [Path(p).expanduser().resolve(strict=True) for p in roots]
        self.config = Path(config)
        self.stats_path = self.config.parent / "stats.json"
        self.lock = threading.Lock()
        self.paths = []
        self.stats_cache = {"version": 1, "repos": {}}
        if self.config.exists():
            raw = json.loads(self.config.read_text())
            self.paths = [str(self.checked(p)) for p in raw.get("paths", [])]
        self.load_stats_cache()

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
        for directory, folders, files in os.walk(root, followlinks=False):
            path = Path(directory)
            if ".git" in folders or ".git" in files:
                found.append({"name": path.name, "path": str(path.resolve())})
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
        return sorted(found, key=lambda r: r["path"])

    def select(self, values):
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("Select at most 100 repositories.")
        paths = []
        for value in values:
            path = self.checked(value)
            if not (path / ".git").exists() or git(
                path, "rev-parse", "--show-toplevel"
            ) != str(path):
                raise ValueError("Select a Git repository root.")
            if str(path) not in paths:
                paths.append(str(path))
        self.config.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.with_suffix(".tmp")
        temporary.write_text(json.dumps({"paths": paths}, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.config)
        self.paths = paths
        kept = {
            path: payload
            for path, payload in self.stats_cache.get("repos", {}).items()
            if path in paths
        }
        if kept != self.stats_cache.get("repos", {}):
            self.stats_cache = {"version": 1, "repos": kept}
            self.save_stats_cache()

    def load_stats_cache(self):
        if not self.stats_path.exists():
            self.stats_cache = {"version": 1, "repos": {}}
            return
        try:
            raw = json.loads(self.stats_path.read_text())
            repos = raw.get("repos", {}) if isinstance(raw, dict) else {}
            self.stats_cache = {
                "version": 1,
                "repos": repos if isinstance(repos, dict) else {},
            }
        except (OSError, json.JSONDecodeError, TypeError):
            self.stats_cache = {"version": 1, "repos": {}}

    def save_stats_cache(self):
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.stats_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.stats_cache, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.stats_path)

    def cached_stats(self):
        repos = self.stats_cache.get("repos", {})
        return [repos[path] for path in self.paths if path in repos]

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
        self.send_response(status)
        if self.headers_allowed():
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
            if self.command == "GET" and self.path == "/api/status":
                response = {
                    "machine": socket.gethostname(),
                    "roots": [str(p) for p in state.roots],
                    "paths": state.paths,
                }
            elif self.command == "POST" and self.path == "/api/discover":
                response = {"repositories": state.discover(data["root"])}
            elif self.command == "POST" and self.path == "/api/selection":
                state.select(data["paths"])
                response = {"paths": state.paths}
            elif self.command == "POST" and self.path == "/api/scan":
                paths = [str(state.checked(p)) for p in state.paths]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    repos = list(pool.map(inspect_repo, paths))
                response = {
                    "machine": socket.gethostname(),
                    "scannedAt": datetime.now(timezone.utc).isoformat(),
                    "repositories": repos,
                }
            elif self.command == "GET" and self.path == "/api/stats":
                response = {
                    "machine": socket.gethostname(),
                    "repositories": state.cached_stats(),
                }
            elif self.command == "POST" and self.path == "/api/stats":
                if data.get("all"):
                    response = {
                        "machine": socket.gethostname(),
                        "repositories": state.stats_for_all(
                            refresh=bool(data.get("refresh"))
                        ),
                    }
                elif "path" in data:
                    response = {
                        "machine": socket.gethostname(),
                        "repositories": [
                            state.stats_for_path(
                                data["path"], refresh=bool(data.get("refresh"))
                            )
                        ],
                    }
                else:
                    raise ValueError("Provide path or all=true for stats.")
            else:
                return self.send_json(404, {"error": "Unknown endpoint."})
            self.send_json(200, response)
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
        default=str(
            Path(__file__).resolve().parents[1] / ".repo-local" / "settings.json"
        ),
    )
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.state = State(args.root, args.config)
    server.token = secrets.token_urlsafe(32)
    print(f"Repo companion: http://127.0.0.1:{args.port}", flush=True)
    print(f"Pairing token: {server.token}", flush=True)
    print(
        "Open https://repo.theana.online and paste this token in Connect companion.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
