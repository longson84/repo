# Repo

Local dashboard for the Git repositories on **this** machine: sync status (ahead / behind / dirty) and commit stats. Everything runs on loopback; nothing is uploaded.

Requires Python 3.9+ and Git. No Python packages to install.

## Start

From this checkout:

```sh
python3 run repo
```

That starts the companion, serves the UI at `http://127.0.0.1:8766`, opens the browser, and pairs automatically. Keep the terminal open. If ports `48731` / `8766` are already taken, `run repo` stops the old process and starts fresh.

Defaults to scanning under `~/Projects`. Another folder:

```sh
python3 run repo /path/to/folder
```

Several approved roots:

```sh
python3 companion/server.py --root ~/Projects --root /another/folder --ui
```

## Use

1. **Manage tracked repos** — discover under an approved folder, then tick what to track (applies immediately).
2. **Scan** — refresh sync status and portfolio stats for tracked repos.
3. **Stats** — combined activity chart and repo comparison board.
4. **AI** (`#/ai`) — detect local AI discussion stores (Antigravity, Claude, Codex, Cursor).
5. Open a repo row for detail (volume, diff churn, commit timeline).
6. **Languages** (`#/languages`) — sortable repos × languages matrix (code lines via [`scc`](https://github.com/boyter/scc)). Values in thousands (`1,230k`).

Requires `scc` on `PATH` (`brew install scc`).

Discovery walks up to three directory levels and skips hidden folders, dependencies, and build output. Deeper repos can be added by full path.

## What Scan does

- Fetches the current branch’s upstream and reports ahead / behind, modified, and untracked counts.
- Never pulls, pushes, commits, checks out, or stashes.
- Recomputes local stats (history + current-tree LOC via `scc`; stats never fetch remotes).

## Data on this machine

All of this is gitignored under `.repo-local/`:

| File | Purpose |
|------|---------|
| `settings.json` | Tracked repo paths |
| `scan.json` | Last Scan snapshot (survives browser refresh) |
| `stats.json` | Stats cache (reuse ~24h while `HEAD` unchanged) |
| `pairing-token` | Companion pairing secret |
| `ai/detect.json` | Last AI store Detect snapshot |

**Disconnect** in the UI forgets the browser token. Restarting the companion reuses the same token unless you pass `--new-token`.

Each computer has its own `.repo-local/`. There is no cross-machine sharing yet — run the app separately on each machine.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## Layout

- `run` — memorable launcher (`python3 run repo`)
- `dist/` — dashboard UI
- `companion/server.py` — loopback API, discovery, scan, stats, AI detect
- `companion/ai/` — AI store catalog and detection
- `tests/` — companion tests

The companion only accepts local UI origins, a pairing token, and a loopback host. Paths must stay inside the `--root` folders you pass at start.
