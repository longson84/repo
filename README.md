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

1. **Manage tracked repos** — discover under an approved folder, choose tracked workspaces, and group repositories into logical projects.
2. **Scan** — refresh sync status and portfolio stats for tracked repos.
3. **Stats** — combined activity chart and repo comparison board.
4. **AI** (`#/ai`) — detect local AI discussion stores (Antigravity, Claude, Codex, Cursor).
5. Open a repo row for detail (volume, diff churn, commit timeline).
6. **Languages** (`#/languages`) — sortable repos × languages matrix (code lines via [`scc`](https://github.com/boyter/scc)). Values in thousands (`1,230k`).

Requires `scc` on `PATH` (`brew install scc`).

Discovery walks up to three directory levels and skips hidden folders, dependencies, and build output. Deeper repos can be added by full path.

### AI accounting semantics

The AI view distinguishes physical stores and transcript representations from logical conversations and executions. Child/subagent executions contribute token usage but do not inflate the conversation count. Token totals count unique provider/model calls: repeated streaming snapshots and copied provider message IDs are deduplicated.

The Tokens report can group usage by day, week, month, or year. Its project view expands each project into provider/model rows; its model view expands each provider/model into projects. Both show input, cached input, output, cache-write, total token volume, share, and unique-call counts. Usage without project attribution appears as `Unattributed`.

Codex surfaces are derived from rollout metadata (`desktop`, `terminal`, `vscode`, or `exec`). Its per-call totals are reported independently from cumulative counters; any disagreement appears under reconciliation and never silently rewrites token totals.

The first usage scan parses local vendor files and can take longer. Later scans reuse `.repo-local/ai/usage.db`, invalidating only files whose size, nanosecond mtime, parser version, or declared dependencies changed. The cache is rebuildable normalized metadata and counters only—it never stores prompts, responses, snippets, or tool payloads.

### Project identity

Reports use three levels instead of treating every folder name as a separate project:

1. **Project** — the product or body of work shown in totals.
2. **Repository** — a Git repository, identified by its credential-free normalized remote or Git common directory.
3. **Workspace** — a checkout, clone, or worktree path that preserves where the activity occurred.

Resolution is deterministic. A live Git repository is resolved first; explicit repository membership chooses its configured project, otherwise that repository becomes its own project. When no live repository can be read, an exact normalized workspace alias is tried, followed by an unambiguous case-insensitive match. An existing non-Git directory becomes a path-backed project. Missing, ambiguous, or conflicting evidence stays `Unattributed` and produces a stable warning code; folder suffixes are never guessed.

Machine-local grouping rules live in `.repo-local/project-identities.json`. For example:

```json
{
  "version": 1,
  "projects": [
    {
      "id": "projectcompanyq",
      "name": "projectCompanyQ",
      "repositories": ["remote:github.com/longson84/projectcompanyq"],
      "primary_workspaces": {
        "remote:github.com/longson84/projectcompanyq": "/Users/longson/Projects/projectCompanyQ"
      },
      "workspace_aliases": [
        "/Users/longson/Projects/projectCompanyQ-plan-6-google",
        "/Users/longson/Projects/projectCompanyQ-plan-7-tasks"
      ]
    }
  ]
}
```

The companion validates this file and writes it with owner-only permissions. Changing a rule invalidates derived scan/stat caches. AI usage events keep their recorded workspace path in the rebuildable cache, so aliases are reapplied immediately without reparsing transcript content. In **Stats**, expand project → repository → workspace to inspect Git attribution. In **AI → Tokens**, expand project/model rows and open **Attribution quality** to inspect configured, path-based, and unresolved workspace evidence.

### Manage project grouping

Open **Manage tracked repos** to edit grouping without touching the JSON file:

- The **Group** checkbox selects a canonical repository for a bulk project action. Multiple clones or worktrees of the same repository select together.
- The **Tracked** switch controls whether that workspace appears in local reports. Grouping does not change tracking, and tracking does not change grouping.
- **Group selected** can assign repositories to an existing project, create a new project, or make them **Standalone** again.
- Configured project cards support rename, ungroup, and delete. Rename preserves the stable project ID. Delete is disabled when historical workspace aliases or primary-workspace mappings must be retained; ungrouping remains available for live repositories.

Project changes persist locally in `.repo-local/project-identities.json`. Writes use a configuration fingerprint so a stale browser tab cannot overwrite a newer edit. After a save, the dashboard refreshes repository statistics. Previously loaded AI totals are cleared because their project attribution is stale; open **AI → Tokens** and run **Aggregate** again. If the refresh fails, the grouping remains saved and the normal **Scan** button retries the report refresh.

## What Scan does

- Reads existing local and remote-tracking refs to report ahead / behind, modified, and untracked counts; it does not fetch.
- Never pulls, pushes, commits, checks out, or stashes.
- Recomputes local stats (history + current-tree LOC via `scc`; stats never fetch remotes).

## Data on this machine

All of this is gitignored under `.repo-local/`:

| File | Purpose |
|------|---------|
| `settings.json` | Tracked repo paths |
| `scan.json` | Last Scan snapshot (survives browser refresh) |
| `stats.json` | Stats cache (reuse ~24h while `HEAD` unchanged) |
| `project-identities.json` | Validated project/repository/workspace grouping rules (mode `0600`) |
| `pairing-token` | Companion pairing secret |
| `ai/detect.json` | Last AI store Detect snapshot |
| `ai/usage.db` | Rebuildable normalized token-usage cache (no transcript text) |

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
- `companion/ai/` — AI store catalog, identity normalization, detection, usage, and cache
- `tests/` — companion tests

The companion only accepts local UI origins, a pairing token, and a loopback host. Paths must stay inside the `--root` folders you pass at start.
