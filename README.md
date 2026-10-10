# Repo

Track selected Git repositories on the computer you are currently using.

Dashboard: https://repo.theana.online · GitHub: https://github.com/longson84/repo

## Start on each machine

Requires Python 3.9+ and Git. No Python packages need installing.

```sh
cd /path/to/Projects/repo
python3 companion/server.py --root ~/Projects
```

Keep the terminal running. Open the dashboard, choose **Connect companion**, and paste the token printed in your terminal. Allow browser access to localhost if prompted. Pairing is stored only in the current tab’s memory; reconnect after refreshing. The token changes when the service restarts.

The companion listens at `127.0.0.1:48731`. The public website does not run Git; the companion runs it on the machine where it is installed. Git uses that machine’s existing remote authentication.

## Choose repositories

1. Open **Manage tracked repos**.
2. Enter a discovery folder within an approved root and click **Discover repositories**.
3. Tick the repositories you want, or add a repository by its full local path.
4. **Save tracked repos**, then **Scan this machine**.

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

- **Load portfolio stats** computes stats for every tracked path and fills the portfolio sums and table columns.
- Opening a repository opens the stats panel; missing stats are computed on demand. **Refresh stats** forces a recompute for that path (or all paths from the toolbar).
- Results are cached in ignored `.repo-local/stats.json` on that machine. A cache entry is reused for 24 hours while `HEAD` is unchanged; a new commit or an explicit refresh invalidates it.
- Portfolio totals sum only repositories that already have cached stats. List and detail views share the same cache entry per absolute path. Stats never fetch remotes and are not merged across machines.

## Local preview and tests

In another terminal:

```sh
python3 -m http.server 8766 --bind 127.0.0.1 --directory dist
```

Open http://127.0.0.1:8766 and pair with the same companion.

```sh
python3 -m unittest discover -s tests -v
```

The companion accepts only the hosted dashboard and the documented local-preview origins, with a pairing token and a loopback Host header. Paths are resolved against roots approved on the command line. Unauthenticated or unrelated-origin requests cannot inspect repositories or change the selection.

## Source and deployment

- `dist/index.html`: dashboard layout and styling.
- `dist/app.js`: pairing, selection, scanning, stats, and status rendering.
- `companion/server.py`: local discovery, Git scanner, and stats collector.
- `.openai/hosting.json`: existing Sites project identity; preserve it when publishing updates.

GitHub stores the project source. Hosting remains through Sites with private access and the custom domain `repo.theana.online`. Deployments use the Sites-managed source repository; pushing GitHub does not automatically deploy. Local settings and pairing tokens must never be included in deployment artifacts.
