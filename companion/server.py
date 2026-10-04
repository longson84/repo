#!/usr/bin/env python3
"""Loopback-only repository scanner. No checkout, pull, commit, or push endpoints."""

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


def git(path, *args):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        timeout=35,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
    )
    if result.returncode:
        raise ValueError("Git operation failed: " + args[0])
    output = result.stdout.decode("utf-8", errors="replace")
    return output if "-z" in args else output.strip()


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


class State:
    def __init__(self, roots, config):
        self.roots = [Path(p).expanduser().resolve(strict=True) for p in roots]
        self.config = Path(config)
        self.lock = threading.Lock()
        self.paths = []
        if self.config.exists():
            raw = json.loads(self.config.read_text())
            self.paths = [str(self.checked(p)) for p in raw.get("paths", [])]

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
