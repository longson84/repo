# Repo

Track selected Git repositories on the computer you are currently using.

Dashboard: https://repo.theana.online · GitHub: https://github.com/longson84/repo

## Start on each machine

Requires Python 3.9+ and Git. No Python packages need installing.

**Local (recommended):** from this repo root, one memorable command starts the companion, serves the dashboard, opens the browser, and pairs automatically:

```sh
python3 run repo
```

Same thing, longer form:

```sh
python3 companion/server.py --root ~/Projects --ui
```

Keep that terminal running. The browser opens `http://127.0.0.1:8766` already paired. Refresh keeps the connection; use **Disconnect** in the UI to forget the token. The pairing token is stored in ignored `.repo-local/pairing-token` and reused across companion restarts. Pass `--new-token` when you want to rotate it.

**Hosted dashboard:** start without `--ui`, open https://repo.theana.online, choose **Connect companion**, and paste the token once. The browser remembers it in `localStorage` until you disconnect.

```sh
python3 companion/server.py --root ~/Projects
```

The companion listens at `127.0.0.1:48731`. The public website does not run Git; the companion runs it on the machine where it is installed. Git uses that machine’s existing remote authentication.

## Choose repositories

1. Open **Manage tracked repos**.
2. Enter a discovery folder within an approved root and click **Discover repositories**.
3. Tick the repositories you want (or untick to stop tracking) — changes apply immediately. You can also add a repository by full local path.
4. **Scan** (sync status + portfolio stats).

Discovery searches up to three nested directory levels and skips repository internals, hidden folders, dependencies, and build output. An individual repository root can be added explicitly when it is deeper.

Only selected repos are scanned. Settings live in ignored `.repo-local/settings.json` inside this checkout. Each computer has its own selection. To approve multiple folders:

```sh
python3 companion/server.py --root ~/Projects --root /another/folder
```

## Scan behavior

The scanner fetches the current branch’s upstream remote and reports its ahead/behind counts, modified files, and untracked files. It never pulls, pushes, commits, checks out branches, or auto-stashes. Missing upstreams and failed fetches display an error with unknown counts. The sync preview shows clean branches eligible for future fast-forward updates.

Other local branches, stashes, ignored files, and databases are not included. A second machine’s report is not shared to this one yet; open the dashboard and scan on each machine separately.

## Repository stats

Stats are separate from the sync scan. After pairing, the dashboard can load local Git statistics for tracked repositories (commit counts, recent activity, contributors, size, languages, branches, remotes, stashes, and recent churn).

- **Scan** refreshes sync status and recomputes portfolio stats for every tracked path.
- **Stats** (`#/stats`) combines activity across tracked repos (day/week/month chart + top repos) and a sortable comparison board (7d / 30d / all / last commit / 30-day sparkline).
- Click a repository name or **Open** to open its detail page (`#/repo?<path>`). Missing stats are computed on demand; **Refresh stats** on a detail page forces a recompute.
- The detail page shows volume/age, burst vs quiet weeks, and a commit timeline by **day**, **week**, **month**, and **year**.
- Results are cached in ignored `.repo-local/stats.json` on that machine. A cache entry is reused for 24 hours while `HEAD` is unchanged; a new commit or an explicit refresh invalidates it. Older cache entries without the timeline are recomputed automatically.
- The latest **Scan** snapshot is cached in `.repo-local/scan.json`, so a browser refresh restores the last sync table without scanning again.
- Portfolio totals sum only repositories that already have cached stats. List and detail views share the same cache entry per absolute path. Stats never fetch remotes and are not merged across machines.

## Local preview and tests

`--ui` already serves `dist/` on port 8766. To preview the UI alone (without auto-pair):

```sh
python3 -m http.server 8766 --bind 127.0.0.1 --directory dist
```

```sh
python3 -m unittest discover -s tests -v
```

The companion accepts only the hosted dashboard and the documented local-preview origins, with a pairing token and a loopback Host header. Paths are resolved against roots approved on the command line. Unauthenticated or unrelated-origin requests cannot inspect repositories or change the selection.

## Source and deployment

- `dist/index.html`: dashboard layout and styling.
- `dist/app.js`: pairing, selection, scanning, stats, and status rendering.
- `companion/server.py`: local discovery, Git scanner, and stats collector.
- `.openai/hosting.json`: existing Sites project identity; preserve it when publishing updates.

GitHub stores the project source. Hosting remains through Sites with private access and the custom domain `repo.theana.online`. Deployments use the Sites-managed source repository; pushing GitHub does not automatically deploy. Local settings and pairing tokens (`.repo-local/`) must never be included in deployment artifacts.
