# GitHub-only repository tracking design

## Purpose

Repo will track only repositories selected by the user that have at least one remote on
`github.com`. The same selected canonical repository set will define the scope of repository
status, commit statistics, logical project grouping, AI sessions, AI transcript search, and AI
token usage.

This removes path-derived pseudo-projects such as temporary Codex folders and prevents different
views from applying different meanings of “project.”

## Product rules

1. A workspace qualifies only when it exists, is a readable Git worktree, and at least one
   configured Git remote normalizes to `github.com/<owner>/<repository>`.
2. A qualifying workspace appears in reports only after the user selects it under **Manage tracked
   repos**.
3. Selecting one clone or worktree selects the canonical GitHub repository. AI activity from every
   workspace resolving to that same canonical repository is included.
4. Missing or inaccessible paths, ordinary directories, local-only Git repositories, and
   repositories with only non-GitHub remotes are removed from the tracked selection.
5. Inactive project configuration is permanently pruned. The app does not retain hidden
   memberships or historical-only alias projects for repositories outside the selected GitHub set.
6. Filtering is enforced by the local companion. Individual browser views do not independently
   decide which repositories qualify.

## Canonical GitHub identity

The companion enumerates all remotes for a worktree, including separate fetch and push URLs, and
normalizes supported HTTPS and SSH forms without retaining credentials, query strings, or fragments.

Canonical selection is deterministic:

1. use `origin` when any fetch or push URL for `origin` normalizes to `github.com`;
2. otherwise inspect remotes in remote-name order and use the first normalized GitHub remote;
3. if no GitHub remote is available, the workspace is ineligible.

The resulting repository ID has the existing form:

```text
remote:github.com/<owner>/<repository>
```

GitHub repository identity, not the selected filesystem path, is the allowlist key. Separate clones
and linked worktrees therefore share one identity. If an event resolves to the allowlisted identity,
it is included even when its exact workspace path was not selected.

## Reconciliation and destructive migration

On companion startup, and before serving tracked-data APIs, the companion reconciles persisted
selection and project identity state while holding the existing state lock.

Selection reconciliation:

- inspect every saved path;
- retain only paths resolving to an eligible GitHub repository;
- permanently remove missing, inaccessible, non-Git, local-only, and non-GitHub paths;
- build the selected canonical repository-ID allowlist from the retained paths;
- write the normalized selection atomically when it changes.

Project configuration reconciliation:

- remove repository memberships whose canonical repository ID is not selected;
- remove primary-workspace mappings for removed memberships;
- retain a workspace alias only when its current path resolves to a selected canonical GitHub
  repository;
- remove missing, inaccessible, non-Git, local-only, non-GitHub, and ambiguous aliases;
- delete any project left with no selected repository membership;
- write the normalized identity document atomically when it changes.

This migration is intentionally destructive and idempotent. Once an entry is pruned, selecting that
repository later does not restore its previous grouping automatically. A crash between the two
atomic file writes can leave extra identity configuration temporarily, but the next reconciliation
finishes pruning it; excluded data never enters a report because report authorization comes from the
selected repository allowlist.

Derived scan, statistics, and AI aggregation caches are invalidated whenever reconciliation changes
selection or identity configuration.

## Discovery and selection

`POST /api/discover` inspects candidate Git worktrees beneath the approved root and returns only
eligible GitHub repositories. Its response includes `excluded_candidate_count`, but it does not
expose excluded candidates as selectable rows.

`POST /api/selection` validates every submitted path with the same eligibility function. Ineligible
paths are removed rather than persisted. The response returns:

- the retained workspace paths;
- their canonical workspace and repository identities;
- reconciliation counts describing removed paths and project mappings.

The companion retains the latest reconciliation counts in memory. `GET /api/status` returns that
summary so the browser can report startup cleanup once without preserving removed entries.

The backend remains authoritative even if a stale or modified browser submits an ineligible path.

## Shared reporting scope

Every tracked-data flow consumes the same selected canonical repository-ID allowlist.

### Repository status and commit statistics

Scan and statistics operate only on retained selected workspaces. Multiple selected workspaces for
the same canonical repository may contribute branch/worktree status, while commit history remains
deduplicated at repository and project levels as it is today.

### Logical projects

Logical projects group selected canonical GitHub repository IDs. A project is present only when it
contains at least one selected repository. The manager no longer shows alias-only, empty, hidden, or
unselected project cards.

### AI sessions

Session adapters may read their normal local stores, but the API returns only conversations with at
least one root usage/transcript record whose workspace resolves to an allowlisted repository ID.
Executions inherit their root conversation’s resolved repository only when existing adapter evidence
supports that relationship. Records without a usable workspace identity are excluded.

### AI transcript search

Search results are returned only when their recorded workspace resolves to an allowlisted repository
ID. Results without a workspace, with a missing path, or from an excluded repository are omitted.
Search does not infer repository identity from prompt text or folder-name suffixes.

### AI token usage

Usage events are deduplicated as today, resolved through the project identity resolver, then filtered
by the selected repository allowlist before aggregation. Totals and all dimensions—period, model,
project, repository, workspace, surface, tool, source, conversation count, execution count, event
count, and unique calls—are computed only from included events.

The usage response exposes scope metadata and exclusion counts so reduced totals are explicit rather
than appearing to be missing data.

### AI detection

Detection continues to report whether supported AI tool stores exist and are readable. It no longer
shows raw conversation, execution, or representation estimates that include activity outside the
selected GitHub allowlist. Repository-scoped counts belong to Sessions and Tokens, where identity has
been resolved.

## UI behavior

The UI consistently describes the scope as **Selected GitHub repositories**.

- The manager displays only eligible GitHub workspaces.
- Discovery reports how many candidates were excluded by the GitHub rule.
- Startup or selection reconciliation reports how many saved paths and project mappings were
  removed.
- Empty repository, Stats, Sessions, Search, and Tokens states direct the user to select a repository
  with a `github.com` remote.
- Tokens includes a persistent scope note and excluded-event count.
- Detect describes source availability without raw global session totals.
- No report renders path-derived folders as projects.

## Error and race handling

- Eligibility checks tolerate individual Git command failures and classify that path as ineligible.
- Selection and identity reconciliation run under the companion state lock.
- Configuration writes retain the existing atomic replacement and owner-only file permissions.
- The project-identity fingerprint changes after reconciliation so an editor opened against older
  configuration receives the existing conflict response rather than overwriting the migration.
- If a selected repository loses its GitHub remote or becomes inaccessible while the companion is
  running, the next reconciliation removes it and invalidates affected caches before returning data.
- An empty allowlist is valid and returns empty reports, not unscoped data.

## Testing

Automated coverage will include:

1. GitHub URL normalization for HTTPS, SSH, credential-bearing, and `.git` forms;
2. qualification through any configured GitHub remote, with GitHub `origin` preferred;
3. rejection of missing, inaccessible, non-Git, local-only, and non-GitHub workspaces;
4. startup and selection reconciliation with atomic persistence and idempotent reruns;
5. pruning inactive memberships, primary mappings, aliases, and empty projects;
6. discovery and selection API enforcement;
7. clone and worktree identity deduplication;
8. inclusion of activity from unselected workspaces sharing an allowlisted canonical repository;
9. consistent allowlist filtering in AI Sessions, Search, and Tokens;
10. Detect output without misleading raw activity estimates;
11. empty-state and reconciliation messaging in the browser UI;
12. cache invalidation and stale-editor fingerprint conflicts after migration.

The full unit suite must pass. A loopback acceptance helper will snapshot settings and identity
configuration, exercise reconciliation and cross-worktree inclusion against temporary repositories,
and restore the original documents in `finally` without printing the pairing token.

## Acceptance criteria

- Only user-selected repositories with a `github.com` remote appear anywhere as tracked projects or
  activity.
- Any GitHub remote qualifies a repository; a GitHub `origin` is the preferred canonical identity.
- Selecting one clone or worktree includes activity from all workspaces with the same canonical
  GitHub repository identity.
- Existing ineligible selections and inactive grouping configuration are permanently removed.
- Temporary Codex folders such as `g`, `h-y`, and dated prompt folders do not appear in repository,
  session, search, or token reports.
- Real selected GitHub repositories such as `claude-plugins-official` remain eligible.
- All reports reconcile to the same selected canonical repository allowlist.
- Empty and exclusion states are explicit; the app never falls back to unscoped activity.
