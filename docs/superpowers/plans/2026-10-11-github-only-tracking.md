# GitHub-only Repository Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make selected canonical `github.com` repositories the sole scope for repository tracking, project grouping, AI sessions, transcript search, and token usage.

**Architecture:** The companion owns one canonical GitHub repository allowlist derived from the current selected workspaces. It reconciles persisted selection and identity configuration before tracked-data requests, then passes the allowlist through the existing AI adapters so every report applies the same scope. The browser renders server-provided reconciliation and scope metadata; it never implements an independent eligibility rule.

**Tech Stack:** Python 3 standard library, Git CLI, SQLite usage cache, vanilla JavaScript, HTML/CSS, `unittest`, Node `vm` UI tests.

**Spec:** `docs/superpowers/specs/2026-10-11-github-only-tracking-design.md`

## Global Constraints

- A workspace qualifies only when it exists, is a readable Git worktree, and any configured remote normalizes to `github.com/<owner>/<repository>`.
- Prefer a GitHub `origin`; otherwise choose the first GitHub remote in deterministic remote-name order.
- Only repositories explicitly selected in **Manage tracked repos** enter the canonical allowlist.
- Selecting one clone or worktree includes all AI activity resolving to that canonical GitHub repository.
- Missing, inaccessible, non-Git, local-only, and non-GitHub paths are permanently removed from selection.
- Inactive memberships, primary mappings, aliases, and empty projects are permanently deleted.
- An empty allowlist returns empty scoped reports; it must never disable filtering.
- The companion is authoritative. Browser code must not reproduce remote parsing or eligibility decisions.
- Preserve atomic owner-only local writes, optimistic identity fingerprints, approved-root restrictions, and read-only Git inspection.
- Never overwrite a malformed identity document during automatic reconciliation; retain the warning and require an explicit repair.
- Do not add dependencies, pricing, network Git operations, repository mutations, push, or deployment.

## Review Focus

- A non-GitHub `origin`, secondary remote, or separate GitHub push URL must qualify and select the GitHub identity; Task 1 pins this.
- Linked worktrees use a `.git` file and must qualify without requiring a `.git` directory; Tasks 1 and 3 pin this.
- An empty `set()` allowlist must exclude every session, search target, and usage event rather than behaving like “no filter”; Tasks 4 and 5 pin this.
- A merged multi-source conversation may contain several physical transcript paths; only paths belonging to an included conversation may be searchable; Task 4 pins this.
- A repository that disappears or loses its GitHub remote between requests must be pruned before stale scan, stats, or AI data is returned; Tasks 2 and 3 pin this.

---

### Task 1: Canonical GitHub remote resolution

**Files:**
- Modify: `companion/project_identity.py:25-115, 264-303`
- Test: `tests/test_project_identity.py`

**Interfaces:**
- Consumes: existing `normalize_remote_url(value) -> Optional[str]` and injected `git_runner(path, *args) -> str`.
- Produces: `is_github_repository_id(value: Any) -> bool` and `resolve_live_repository(workspace_path: str, git_runner=_run_git) -> tuple[Optional[str], str]`.
- Produces: `ProjectIdentityResolver.resolve()` with GitHub-first remote precedence while retaining existing configured/path/unattributed result shapes.

- [ ] **Step 1: Write failing resolver tests**

Add tests proving:

- GitHub `origin` wins over every other remote;
- when `origin` is non-GitHub, the first GitHub remote by remote name wins;
- separate fetch and push URLs are inspected, so a GitHub push URL qualifies its remote;
- HTTPS, SCP-style SSH, `ssh://`, credentials, case, and `.git` normalize to `remote:github.com/<owner>/<repo>`;
- GitHub Enterprise and GitLab do not satisfy `is_github_repository_id`;
- linked worktrees whose `.git` is a file resolve through `git rev-parse --git-common-dir`;
- no usable remote retains the existing `gitdir:` fallback for diagnostic identity resolution.

- [ ] **Step 2: Run tests to verify RED**

Run: `python3 -m unittest tests.test_project_identity -v`

Expected: FAIL because the public helpers and GitHub-first precedence do not exist.

- [ ] **Step 3: Implement the shared resolver helpers**

Enumerate all remote names once and inspect both `git remote get-url --all <name>` and `git remote get-url --push --all <name>`. Normalize each readable URL, select GitHub `origin` first and then sorted GitHub remotes, and only then fall back to the existing first usable non-GitHub remote or `gitdir:` identity. Make `ProjectIdentityResolver._live_repository()` delegate to `resolve_live_repository()`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `python3 -m unittest tests.test_project_identity -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add companion/project_identity.py tests/test_project_identity.py
git commit -m "feat: resolve canonical GitHub repository identities"
```

### Task 2: Reconcile tracked selection and project configuration

**Files:**
- Modify: `companion/project_identity.py:196-225`
- Modify: `companion/server.py:1080-1200, 1333-1465`
- Test: `tests/test_project_identity.py`
- Test: `tests/test_companion.py`

**Interfaces:**
- Consumes: Task 1 `is_github_repository_id()` and `resolve_live_repository()`.
- Produces: `prune_identity_config(config, selected_repository_ids, alias_repository_ids) -> tuple[dict, dict]`.
- Produces: `State.reconcile_tracking(candidate_paths: Optional[Sequence[str]] = None) -> dict`.
- Produces: `State.selected_repository_ids() -> set[str]` and `State.last_reconciliation` with integer keys `removed_paths`, `removed_repository_memberships`, `removed_primary_workspaces`, `removed_workspace_aliases`, and `removed_projects`.

- [ ] **Step 1: Write failing pure pruning tests**

Cover a mixed project document containing selected and unselected GitHub repositories, a primary mapping for each, a live alias resolving to the selected repository, a missing alias, and an alias-only project. Assert the selected membership and live alias survive while every inactive field and empty project is counted and removed without mutating the input.

- [ ] **Step 2: Run the pure tests to verify RED**

Run: `python3 -m unittest tests.test_project_identity.ConfigTests -v`

Expected: FAIL because `prune_identity_config` is undefined.

- [ ] **Step 3: Implement `prune_identity_config`**

Keep the version-1 document shape and unrelated project metadata. A retained alias must map exactly to an ID in `selected_repository_ids`; missing or ambiguous aliases are absent from `alias_repository_ids` and are removed.

- [ ] **Step 4: Write failing State reconciliation tests**

Add tests proving startup and explicit reconciliation:

- tolerate missing saved paths instead of aborting `State.__init__`;
- remove missing, ordinary-directory, local-only, and GitLab paths;
- retain a worktree with a secondary GitHub remote;
- deduplicate the repository allowlist while retaining separately selected workspace paths;
- atomically persist owner-only settings and identity documents;
- invalidate scan/stats caches after either document changes;
- are idempotent on a second run;
- prune a path that loses its GitHub remote between two reconciliations.
- leave a malformed identity document byte-for-byte unchanged while reporting `invalid_identity_config`.

- [ ] **Step 5: Run State tests to verify RED**

Run: `python3 -m unittest tests.test_companion.CompanionTests -v`

Expected: FAIL on missing-path startup and absent reconciliation methods.

- [ ] **Step 6: Implement State reconciliation**

Load raw saved path strings without strict resolution, load identity and derived caches, then call `reconcile_tracking()` during initialization. Resolve aliases against live Git evidence without allowing the alias configuration itself to manufacture eligibility. Reuse the existing atomic writers and cache invalidation paths.

- [ ] **Step 7: Run Task 2 tests to verify GREEN**

Run: `python3 -m unittest tests.test_project_identity tests.test_companion -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/project_identity.py companion/server.py tests/test_project_identity.py tests/test_companion.py
git commit -m "feat: reconcile selected GitHub repositories"
```

### Task 3: Enforce eligibility at HTTP discovery and selection boundaries

**Files:**
- Modify: `companion/server.py:1309-1360, 1640-1750`
- Test: `tests/test_companion.py`
- Test: `tests/test_ai_api.py`

**Interfaces:**
- Consumes: Task 2 `State.reconcile_tracking()` and `State.last_reconciliation`.
- Produces: `State.discover(value) -> dict` with `repositories` and `excluded_candidate_count`.
- Produces: `State.select(values) -> dict` returning the latest reconciliation counts.
- Produces: status and selection responses containing `reconciliation` and canonical `workspaces`.

- [ ] **Step 1: Write failing discovery and selection tests**

Assert that discovery returns only GitHub repositories, counts excluded Git/GitLab/local candidates, recognizes linked worktrees, and never exposes ordinary directories. Assert selection silently removes submitted ineligible paths, persists only eligible paths, and returns exact reconciliation counts.

- [ ] **Step 2: Write failing request-boundary tests**

At HTTP level, prove:

- startup reconciliation appears in `GET /api/status`;
- a stale client cannot save an ineligible path;
- saving project identities prunes unselected memberships before returning the new fingerprint;
- status, scan, stats, project identity, and AI routes reconcile first;
- when the last selected repository disappears, status, scan, stats, project identity, AI Sessions, AI Search, and AI Tokens return empty scoped results rather than cached data.

- [ ] **Step 3: Run tests to verify RED**

Run: `python3 -m unittest tests.test_companion tests.test_ai_api -v`

Expected: FAIL because current discovery exposes all Git repos and selection rejects rather than removes ineligible paths.

- [ ] **Step 4: Implement endpoint enforcement**

Invoke reconciliation once after request validation and while the existing handler lock is held. Do not recursively acquire `State.lock`. Change discovery and selection response assembly to use their new result shapes, and make identity saves normalize against the current selected allowlist.

- [ ] **Step 5: Run tests to verify GREEN**

Run: `python3 -m unittest tests.test_companion tests.test_ai_api -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add companion/server.py tests/test_companion.py tests/test_ai_api.py
git commit -m "feat: enforce GitHub tracking at API boundaries"
```

### Task 4: Scope AI sessions and transcript search

**Files:**
- Modify: `companion/ai/sessions.py:45-230`
- Modify: `companion/ai/search.py:20-465`
- Modify: `companion/server.py:1208-1239, 1285-1308`
- Test: `tests/test_ai_sessions.py`
- Test: `tests/test_ai_search.py`
- Test: `tests/test_ai_api.py`

**Interfaces:**
- Consumes: Task 2 selected repository IDs and the current `ProjectIdentityResolver`.
- Produces: optional keyword parameters `identity_resolver` and `allowed_repository_ids: Optional[set[str]]` on `collect_sessions()` and `list_sessions()`.
- Produces: `allowed_transcript_paths(rows: Sequence[dict]) -> set[str]`.
- Produces: optional `allowed_paths: Optional[set[str]]` on `search_transcripts()`.

- [ ] **Step 1: Write failing session-scope tests**

Build adapter fixtures for an allowlisted GitHub worktree, another worktree of the same repository, an unselected GitHub repository, a scratch folder, and a missing folder. Assert only the first two merge into the response, returned project/repository fields use canonical identity, the project filter runs on canonical labels, and an explicit empty allowlist returns zero conversations.

- [ ] **Step 2: Run session tests to verify RED**

Run: `python3 -m unittest tests.test_ai_sessions -v`

Expected: FAIL because session collection has no canonical scope parameters.

- [ ] **Step 3: Implement pre-merge session scoping**

Resolve and annotate individual representations before `_merge_conversations()` so one valid representation is not discarded because another representation was merged first. Preserve all physical `paths` for included representations and compute response counts after filtering.

- [ ] **Step 4: Write failing search-scope tests**

Assert `allowed_transcript_paths()` includes every physical transcript and child execution path of included merged conversations. Assert both ripgrep and Python/SQLite search paths exclude disallowed targets before applying `limit`, and an empty allowed set searches nothing.

- [ ] **Step 5: Run search tests to verify RED**

Run: `python3 -m unittest tests.test_ai_search -v`

Expected: FAIL because search targets are currently unscoped.

- [ ] **Step 6: Implement target-level search filtering and server wiring**

Have `State.run_ai_search()` collect scoped sessions, derive allowed physical paths, and pass them into search. Have `State.list_ai_sessions()` pass the same resolver and selected allowlist. Do not infer scope from snippets, prompt text, session IDs, or folder suffixes.

- [ ] **Step 7: Run Task 4 tests to verify GREEN**

Run: `python3 -m unittest tests.test_ai_sessions tests.test_ai_search tests.test_ai_api -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/ai/sessions.py companion/ai/search.py companion/server.py tests/test_ai_sessions.py tests/test_ai_search.py tests/test_ai_api.py
git commit -m "feat: scope AI sessions and search to selected repositories"
```

### Task 5: Scope token aggregation and simplify AI detection

**Files:**
- Modify: `companion/ai/usage.py:222-620`
- Modify: `companion/ai/detect.py:1-450`
- Modify: `companion/server.py:1199-1284`
- Test: `tests/test_ai_usage.py`
- Test: `tests/test_ai_detect.py`
- Test: `tests/test_ai_api.py`

**Interfaces:**
- Consumes: Task 4 scoped `collect_sessions()` for selector validation.
- Produces: optional `allowed_repository_ids: Optional[set[str]]` on `aggregate_usage()`.
- Produces: usage `scope` object with `kind`, `repository_count`, `excluded_event_count`, and `excluded_tokens` token bucket.
- Produces: detect payload version 3 containing store availability only; summary keys are `total`, `stores_found`, and compatibility alias `found`.

- [ ] **Step 1: Write failing usage-scope tests**

Use equal-token events for an allowlisted workspace, an alternate worktree of the same GitHub repository, an unselected GitHub repository, GitLab, local Git, ordinary folder, missing path, and absent cwd. Assert only the first two contribute to every aggregate dimension and all conversation/execution/event counts. Assert excluded counts and token buckets reconcile to pre-filter totals. Explicitly assert `allowed_repository_ids=set()` yields zero totals.

- [ ] **Step 2: Run usage tests to verify RED**

Run: `python3 -m unittest tests.test_ai_usage -v`

Expected: FAIL because aggregation currently includes every resolved event.

- [ ] **Step 3: Implement post-dedup, pre-aggregation filtering**

Resolve identities once per unique cwd as today. Partition deduplicated events by exact repository-ID membership before attribution counters or dimension buckets are updated. Validate conversation selectors against the same scoped session inventory.

- [ ] **Step 4: Write failing detect-contract tests**

Replace raw inventory-count expectations with availability assertions. Prove version 3 source and summary payloads omit `session_estimate`, `conversation_estimate`, `execution_estimate`, `representation_estimate`, and `sessions`; prove version-2 cache files are ignored.

- [ ] **Step 5: Run detect tests to verify RED**

Run: `python3 -m unittest tests.test_ai_detect -v`

Expected: FAIL because detect still publishes global activity estimates.

- [ ] **Step 6: Implement availability-only detection and server wiring**

Remove inventory scans from `detect_all()`, bump the cache contract to version 3, and pass the selected allowlist into `aggregate_usage()`. Keep source existence, size, notes, and capability flags.

- [ ] **Step 7: Run Task 5 tests to verify GREEN**

Run: `python3 -m unittest tests.test_ai_usage tests.test_ai_detect tests.test_ai_api -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add companion/ai/usage.py companion/ai/detect.py companion/server.py tests/test_ai_usage.py tests/test_ai_detect.py tests/test_ai_api.py
git commit -m "feat: scope AI analytics to selected GitHub repositories"
```

### Task 6: Make the browser explain the shared scope and migration

**Files:**
- Modify: `dist/app.js:50-125, 730-970, 1240-1570, 2790-3510`
- Modify: `dist/index.html`
- Test: `tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: Tasks 3-5 `reconciliation`, discovery `excluded_candidate_count`, usage `scope`, availability-only detect payload, and scoped empty results.
- Produces: `trackingReconciliationMessage(reconciliation) -> string` and consistent selected-GitHub empty/scope copy.

- [ ] **Step 1: Write failing UI tests**

Assert:

- status and selection cleanup produce a readable removal message with paths and project mappings;
- discovery reports the excluded candidate count;
- manager copy and empty states say `Select a repository with a github.com remote`;
- Detect renders only Surface, Status, Path, Size, and Notes columns;
- Sessions, Search, and Tokens display `Selected GitHub repositories` scope;
- Tokens displays excluded event count from `scope` and renders no path-attribution quality category;
- an empty scoped response cannot reuse previously loaded rows.

- [ ] **Step 2: Run UI tests to verify RED**

Run: `python3 -m unittest tests.test_dashboard_ui -v`

Expected: FAIL on missing reconciliation helpers and old Detect/report copy.

- [ ] **Step 3: Implement the UI contract**

Use response metadata only. Clear stale rows when scoped calls return empty, render cleanup notices as status/toast text, remove raw Detect count columns, and keep existing focus restoration, sorting, and responsive table behavior.

- [ ] **Step 4: Run UI tests and syntax checks to verify GREEN**

Run: `node --check dist/app.js && python3 -m unittest tests.test_dashboard_ui -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dist/app.js dist/index.html tests/test_dashboard_ui.py
git commit -m "feat: explain selected GitHub repository scope"
```

### Task 7: Document, verify, and exercise destructive migration safely

**Files:**
- Modify: `README.md`
- Create: `.superpowers/sdd/2026-10-11-github-only-tracking/api_acceptance.py` (ignored execution artifact)
- Test: entire `tests/` suite

**Interfaces:**
- Consumes: the complete GitHub-only tracking flow.
- Produces: user documentation and recorded local acceptance evidence; no new product interface.

- [ ] **Step 1: Update README behavior and migration warnings**

Document any-remote GitHub eligibility, explicit selection, cross-worktree inclusion, destructive startup pruning, shared Stats/Sessions/Search/Tokens scope, availability-only Detect, and the empty-state recovery path. Remove statements that describe arbitrary local folders or non-GitHub repositories as trackable.

- [ ] **Step 2: Run static and full automated verification**

Run:

```bash
node --check dist/project_groups.js
node --check dist/app.js
npx --yes prettier@3.8.1 --check dist/index.html dist/app.js dist/project_groups.js
uvx ruff format --check companion tests
git diff --check
python3 -m unittest discover -s tests -v
```

Expected: all commands pass.

- [ ] **Step 3: Write the guarded loopback acceptance helper**

The ignored helper must snapshot `settings.json` and `project-identities.json`, then in `try/finally`:

- create temporary GitHub, GitLab, local-only, ordinary-directory, and alternate-worktree fixtures under an approved temporary root;
- run a temporary companion instance on an available loopback port with a temporary token file;
- submit mixed selection and identity documents;
- verify only the GitHub selection and active project survive;
- verify the alternate worktree shares the canonical repository identity;
- verify status/stats and fixture AI Sessions/Search/Tokens contain only the selected identity;
- verify Detect exposes availability without raw activity counts;
- restore original local documents byte-for-byte if the real local files are touched;
- never print the bearer token or transcript contents.

- [ ] **Step 4: Run loopback acceptance**

Run: `python3 .superpowers/sdd/2026-10-11-github-only-tracking/api_acceptance.py`

Expected: `API acceptance passed: all views use the selected GitHub repository scope and snapshots were restored`.

- [ ] **Step 5: Run final full-suite verification after acceptance**

Run: `python3 -m unittest discover -s tests -v && git diff --check`

Expected: PASS and no acceptance-induced tracked changes.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: explain GitHub-only repository tracking"
```

- [ ] **Step 7: Request one whole-change review**

Review the full implementation range against the spec, with special attention to the five Review Focus cases, destructive reconciliation, empty-allowlist semantics, cache invalidation, and evidence that every endpoint shares one allowlist. Fix Critical and Important findings with regression tests in one final pass; record Minor findings explicitly.
