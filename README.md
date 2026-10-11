# Repo

Local dashboard for explicitly selected `github.com` repositories on **this** machine: sync status (ahead / behind / dirty), commit stats, and repository-scoped AI activity. Everything runs on loopback; nothing is uploaded.

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

1. **Manage tracked repos** — discover eligible GitHub workspaces under an approved folder, explicitly select them, and group their canonical repositories into logical projects.
2. **Scan** — refresh sync status and portfolio stats for tracked repos.
3. **Stats** — combined activity chart and repo comparison board.
4. **AI** (`#/ai`) — detect local AI store availability, then report conversations, transcript matches, and tokens only for selected GitHub repositories.
5. Open a repo row for detail (volume, diff churn, commit timeline).
6. **Languages** (`#/languages`) — sortable logical-project × languages matrix with expandable repository rows (code lines via [`scc`](https://github.com/boyter/scc)). Values in thousands (`1,230k`).

Requires `scc` on `PATH` (`brew install scc`).

Discovery walks up to three directory levels and skips hidden folders, dependencies, and build output. A workspace is eligible only when any configured fetch or push remote normalizes to `github.com/<owner>/<repository>`. A GitHub `origin` wins; otherwise the first GitHub remote by name is used. Deeper eligible repositories can be submitted by full path.

Selection is explicit. Finding a repository does not track it. Selecting one clone or linked worktree authorizes the canonical GitHub repository, so AI activity recorded from another live clone or worktree of that same repository is included.

### AI accounting semantics

**Detect** reports whether known local stores exist, their paths and sizes, and supported capabilities. It intentionally does not show global conversation, execution, or representation estimates because those stores may contain activity outside the tracked repository scope.

Sessions, Search, and Tokens all use the same **Selected GitHub repositories** allowlist as repository status and Stats. Records without a live workspace identity, records from unselected repositories, local-only Git work, non-GitHub work, missing paths, and ordinary folders are excluded. The Tokens report shows the excluded event count and excluded token bucket so the reduced total is explicit.

The AI view distinguishes physical transcript representations from logical conversations and executions. Child/subagent executions contribute token usage but do not inflate the conversation count. Token totals count unique provider/model calls: repeated streaming snapshots and copied provider message IDs are deduplicated.

The Tokens report can group usage by day, week, month, or year. Its project view expands each project into provider/model rows; its model view expands each provider/model into projects. Both show input, cached input, output, cache-write, total token volume, share, and unique-call counts. Usage without project attribution appears as `Unattributed`.

Codex surfaces are derived from rollout metadata (`desktop`, `terminal`, `vscode`, or `exec`). Its per-call totals are reported independently from cumulative counters; any disagreement appears under reconciliation and never silently rewrites token totals.

The first usage scan parses local vendor files and can take longer. Later scans reuse `.repo-local/ai/usage.db`, invalidating only files whose size, nanosecond mtime, parser version, or declared dependencies changed. The cache is rebuildable normalized metadata and counters only—it never stores prompts, responses, snippets, or tool payloads.

### Project identity

Reports use three levels instead of treating every folder name as a separate project:

1. **Project** — the product or body of work shown in totals.
2. **Repository** — an eligible canonical `remote:github.com/<owner>/<repository>` identity.
3. **Workspace** — a checkout, clone, or worktree path that preserves where the activity occurred.

Resolution is deterministic. The companion inspects every remote's fetch and push URLs, prefers a GitHub `origin`, then chooses the first GitHub remote by remote name. Explicit repository membership chooses its configured logical project; otherwise that canonical GitHub repository is its own project. Folder names and prompt-derived paths are never treated as repository identity.

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

The companion validates this file and writes it with owner-only permissions. Changing a rule invalidates derived scan/stat caches. AI usage events keep their recorded workspace path in the rebuildable cache, so project rules are reapplied without reparsing transcript content. In **Stats**, expand project → repository → workspace to inspect Git attribution. In **AI → Tokens**, expand project/model rows and open **Attribution quality** to inspect configured and unresolved workspace evidence.

### GitHub-only migration

On startup and before tracked-data requests, the companion reconciles selection and grouping state. This cleanup is intentionally destructive and idempotent:

- missing, inaccessible, ordinary-directory, local-only, and non-GitHub paths are removed from `settings.json`;
- repository memberships outside the selected canonical GitHub set are removed;
- stale primary-workspace mappings, aliases, and projects left empty are deleted;
- scan, Stats, and AI-derived caches are invalidated when reconciliation changes configuration.

A malformed `project-identities.json` is never overwritten automatically; fix it explicitly. Pruned grouping is not restored if a repository is selected again later. If every selection is removed, all reports remain empty—there is no fallback to unscoped local activity. Recover by opening **Manage tracked repos** and selecting a repository with a `github.com` remote.

### Manage project grouping

Open **Manage tracked repos** to edit grouping without touching the JSON file:

- The **Group** checkbox selects a canonical repository for a bulk project action. Multiple clones or worktrees of the same repository select together.
- The **Tracked** switch controls whether the canonical GitHub repository appears in local reports. Selecting one clone or worktree includes every live workspace with the same canonical identity in AI reports.
- **Group selected** can assign repositories to an existing project, create a new project, or make them **Standalone** again.
- Configured project cards support rename, ungroup, and delete. Rename preserves the stable project ID. Delete is disabled when historical workspace aliases or primary-workspace mappings must be retained. Ungroup is also disabled when it would discard a primary-workspace mapping; remove that mapping from the local identity file first.

Repository tracking and project changes are staged inside **Manage tracked repos**. Selecting repositories opens the project pane on the right, and repeated grouping edits do not save or scan individually. **Save & scan** persists the complete draft, closes the manager, and refreshes repository statistics once. Closing with staged changes asks whether to save, discard, or continue editing.

Project changes persist locally in `.repo-local/project-identities.json`. Writes use a configuration fingerprint so a stale browser tab cannot overwrite a newer edit. Previously loaded AI totals are cleared because their project attribution is stale; open **AI → Tokens** and run **Aggregate** again. If the refresh fails, the grouping remains saved and the normal **Scan** button retries the report refresh.

## What Scan does

- Reads existing local and remote-tracking refs to report ahead / behind, modified, and untracked counts; it does not fetch.
- Never pulls, pushes, commits, checks out, or stashes.
- Recomputes local stats (history + current-tree LOC via `scc`; stats never fetch remotes).

## Data on this machine

All of this is gitignored under `.repo-local/`:

| File | Purpose |
|------|---------|
| `settings.json` | Selected eligible GitHub workspace paths |
| `scan.json` | Last Scan snapshot (survives browser refresh) |
| `stats.json` | Stats cache (reuse ~24h while `HEAD` unchanged) |
| `project-identities.json` | Validated project/repository/workspace grouping rules (mode `0600`) |
| `pairing-token` | Companion pairing secret |
| `ai/detect.json` | Last AI store Detect snapshot |
| `ai/usage.db` | Rebuildable normalized token-usage cache (no transcript text) |

**Disconnect** in the UI forgets the browser token. Restarting the companion reuses the same token unless you pass `--new-token`.

Each computer runs independently with its own `.repo-local/`, tracked selection, scans, and reports.

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
