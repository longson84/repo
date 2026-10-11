# Shared Project Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give repository statistics and AI token reports one canonical workspace → repository → project identity model, deduplicate shared Git history, and support both project/model drill-down directions.

**Architecture:** Add a local-only identity resolver and configuration store used by both the repository scanner and AI usage aggregator. Workspace records retain checkout-specific state; repository records own unique-SHA history; project records aggregate one or more repositories. Derived caches are versioned and rebuildable, while `.repo-local/project-identities.json` is durable user configuration.

**Tech Stack:** Python 3 standard library, local Git CLI, SQLite usage cache, `unittest`, vanilla JavaScript/CSS/HTML.

**Spec:** `docs/superpowers/specs/2026-10-11-shared-project-identity-design.md`

## Global Constraints

- Execute directly on `main`, as requested. Do not create a worktree or feature branch.
- Preserve the existing uncommitted pricing-removal and token-report changes. Inspect and commit them as the baseline before identity work; never reset or overwrite them.
- Use only local, read-only Git commands for identity and statistics. Do not fetch, pull, checkout, create branches/worktrees, or mutate repositories.
- Never infer project membership by stripping suffixes. Live Git metadata and explicit configuration are the only merging authorities.
- Keep transcript content, prompts, responses, snippets, tool payloads, remote credentials, query strings, and fragments out of caches and API responses.
- Keep selected repository paths in `settings.json`; identity configuration must not add or remove tracked paths.
- Every task follows red → green → focused regression → commit. Stage only files owned by that task.

## Review Focus

1. Case variants, missing historical paths, and exact aliases merge only when evidence is unambiguous; conflicts remain separate with warnings.
2. Credential-bearing, malformed, and remote-less Git URLs never leak secrets and produce stable fallback identities.
3. Multiple worktrees/clones deduplicate shared ancestry by SHA while including commits reachable only from another tracked tip, including detached HEAD.
4. Project and portfolio current-tree metrics use exactly one deterministic primary workspace per repository.
5. Cache/API migration preserves tracked selections and durable identity configuration, and all token/commit parent-child totals reconcile.

---

## File Structure

### New files

- `companion/project_identity.py` — configuration validation, path/remote normalization, live and historical workspace resolution, project assignment, warnings, and confidence.
- `companion/commit_stats.py` — unique commit-record collection and aggregation across the union of tracked repository tips.
- `tests/test_project_identity.py` — resolver, configuration, normalization, ambiguity, and security tests.
- `tests/test_commit_stats.py` — synthetic Git repository/worktree/clone history and primary-workspace tests.

### Existing files to modify

- `companion/server.py:281-321,602-845,972-1170,1330-1410` — SHA-aware parsing, state/cache migration, canonical hierarchy APIs, and identity configuration endpoint.
- `companion/ai/usage.py:127-180,217-end` — shared attribution and project/repository/workspace plus inverse model/project aggregation.
- `companion/ai/usage_cache.py:12-84` — source-evidence allowlist and privacy regression coverage; canonical attribution is deliberately not persisted.
- `companion/ai/adapters/antigravity.py`, `claude_code.py`, `codex.py` — consistently retain recorded `cwd`; increment parser versions only where normalized output changes.
- `dist/app.js:363-520,648-1220,1839-1960` — canonical repository totals, hierarchy rendering, inverse model drill-down, filters, and warning details.
- `dist/index.html` — hierarchy and attribution-quality styles/labels.
- `tests/test_companion.py` — state, API, cache migration, deduplicated statistics, and compatibility tests.
- `tests/test_ai_usage.py` — canonical attribution and nested rollup reconciliation.
- `tests/test_ai_usage_cache.py` — schema rebuild and privacy tests.
- `tests/test_ai_api.py` — identity-config and aggregate response-contract tests.
- `README.md` — identity model, local configuration, migration, and token-report usage.

## Task 0: Preserve and Close the Existing Token-Only Baseline

**Files:**

- Modify only as needed to fix a regression: current modified/deleted files shown by `git status --short`
- Verify: `tests/test_ai_usage.py`, `tests/test_ai_api.py`, `tests/test_companion.py`

**Interfaces:** No new interface. The baseline contract is that runtime/API/UI have no pricing fields and period → project → model already works.

- [ ] Inspect `git diff --check`, `git diff --stat`, and the full diff. Confirm all current changes belong to the previously approved pricing-removal/token-report work.
- [ ] Run the current focused suite and record the expected green baseline:

  ```bash
  python3 -m unittest tests.test_ai_usage tests.test_ai_api tests.test_companion
  node --check dist/app.js
  rg -n "estimated_cost|pricing_status|/api/ai/prices|EST\. \$" companion dist README.md
  ```

  Expected: tests pass, JavaScript parses, and the final search has no runtime/API/UI hits (historical design/plan prose may be excluded explicitly).

- [ ] If a regression appears, add the smallest failing assertion to the owning existing test, reproduce it, and make only the minimal baseline correction.
- [ ] Run the full suite:

  ```bash
  python3 -m unittest discover -s tests -v
  ```

  Expected: all tests pass.

- [ ] Commit only the existing baseline files:

  ```bash
  git add README.md companion/ai companion/server.py dist tests docs/superpowers/plans/2026-10-11-ai-accounting-corrections.md docs/superpowers/plans/2026-10-11-ai-local-history.md docs/superpowers/specs/2026-10-11-ai-accounting-corrections-design.md docs/superpowers/specs/2026-10-11-ai-local-history-design.md
  git commit -m "feat: report AI usage with tokens only"
  ```

## Task 1: Build the Shared Identity Resolver and Durable Config Store

**Files:**

- Create: `companion/project_identity.py`
- Create: `tests/test_project_identity.py`

**Interfaces:**

```python
def canonicalize_workspace_path(value: str, *, must_exist: bool = False) -> str: ...
def normalize_remote_url(value: str) -> Optional[str]: ...
def load_identity_config(path: Path) -> dict[str, Any]: ...
def save_identity_config(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]: ...

class ProjectIdentityResolver:
    def __init__(self, config: Mapping[str, Any], *, git_runner: Callable[..., str] = ...): ...
    def resolve(self, workspace_path: str) -> dict[str, Any]: ...
    def fingerprint(self) -> str: ...
```

The resolved record must contain `workspace_id`, `workspace_name`, `repository_id`, `repository_name`, `project_id`, `project_name`, `identity_confidence`, and `identity_warnings`.

- [ ] Write failing tests for: symlink and case canonicalization; same Git common directory; independent same-basename repositories; SSH/HTTPS remote equivalence; credential/query/fragment stripping; remote-less fallback; deleted exact aliases; and conflicting membership remaining unmerged.
- [ ] Add config validation tests for duplicate project IDs, a repository assigned to two projects, an alias assigned to two projects, malformed version/type data, and atomic mode-`0600` replacement.
- [ ] Run:

  ```bash
  python3 -m unittest tests.test_project_identity -v
  ```

  Expected: failures because the module does not exist.

- [ ] Implement the minimal resolver. Use `git rev-parse --path-format=absolute --git-common-dir`, `git remote`, and `git remote get-url` only for existing workspaces. Normalize SCP-style and URL-style remotes before choosing `remote:`; otherwise choose `gitdir:`. Never persist raw remote values.
- [ ] Implement precedence exactly: live repository membership → exact normalized workspace alias → unambiguous case-folded path compatibility → default one-project-per-repository/path. Emit stable warning codes for conflicts instead of choosing arbitrarily.
- [ ] Run the focused test, then the full suite:

  ```bash
  python3 -m unittest tests.test_project_identity -v
  python3 -m unittest discover -s tests -v
  ```

- [ ] Commit:

  ```bash
  git add companion/project_identity.py tests/test_project_identity.py
  git commit -m "feat: add shared project identity resolver"
  ```

## Task 2: Add Identity Configuration and Canonical Inventory to State/API

**Files:**

- Modify: `companion/server.py:972-1170,1330-1410`
- Modify: `tests/test_companion.py`
- Modify: `tests/test_ai_api.py`

**Interfaces:**

```python
State.identity_path: Path
State.identity_config: dict[str, Any]
State.identity_resolver: ProjectIdentityResolver

def State.reload_identity_config(self) -> dict[str, Any]: ...
def State.save_identity_config(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...
def State.workspace_inventory(self) -> list[dict[str, Any]]: ...
```

HTTP contract:

- `GET /api/project-identities` returns validated durable configuration plus current warnings.
- `POST /api/project-identities` validates and atomically replaces configuration, then invalidates derived caches only.
- Status/discovery/selection/scan records add canonical identity fields while retaining `path`, `name`, and `paths` compatibility fields.

- [ ] Write failing state/API tests for loading a missing config, owner-only atomic save, invalid/conflicting config returning 400, selection preservation, identity cache invalidation, and compatibility fields.
- [ ] Add a test proving that saving identity configuration neither modifies `settings.json` nor runs mutating Git commands.
- [ ] Run:

  ```bash
  python3 -m unittest tests.test_companion tests.test_ai_api -v
  ```

  Expected: new endpoint and state tests fail.

- [ ] Wire `ProjectIdentityResolver` into `State`. Cache one resolver per configuration fingerprint and one resolved result per canonical workspace path during a request/scan.
- [ ] Add the two authenticated local API routes with the existing body-size and origin/token protections. Return warning codes and affected IDs, not sensitive raw Git configuration.
- [ ] Bump stats and scan cache versions from `1` to `2`. On old-version load, rebuild derived data while retaining `self.paths` from `settings.json` and leaving the identity file untouched.
- [ ] Run the focused tests and full suite, then commit:

  ```bash
  python3 -m unittest tests.test_companion tests.test_ai_api -v
  python3 -m unittest discover -s tests -v
  git add companion/server.py tests/test_companion.py tests/test_ai_api.py
  git commit -m "feat: expose canonical project inventory"
  ```

## Task 3: Deduplicate Commit History and Current-Tree Metrics

**Files:**

- Create: `companion/commit_stats.py`
- Create: `tests/test_commit_stats.py`
- Modify: `companion/server.py:281-321,674-960,1170-1325`
- Modify: `tests/test_companion.py`

**Interfaces:**

```python
def parse_commit_numstat(text: str) -> list[dict[str, Any]]: ...
def collect_workspace_tip(path: str, *, git_runner: Callable[..., str] = ...) -> Optional[str]: ...
def collect_repository_commits(workspace_paths: Sequence[str], *, git_runner: Callable[..., str] = ...) -> list[dict[str, Any]]: ...
def aggregate_repository_history(repository_id: str, commits: Sequence[Mapping[str, Any]], *, now: Optional[datetime] = None) -> dict[str, Any]: ...
def choose_primary_workspace(workspaces: Sequence[Mapping[str, Any]], configured_path: Optional[str]) -> Optional[str]: ...
```

Commit records must carry `sha`, `iso`, author identity used by existing contributor metrics, additions, and deletions. Deduplication key inside a repository is `sha`; project/portfolio identity is `(repository_id, sha)`.

- [ ] Create synthetic Git tests with one base repository, two linked worktrees on divergent branches, shared ancestry, and a detached worktree tip. First assert the current path-sum behavior double-counts ancestry.
- [ ] Add tests for: every SHA counted once; branch-only commits included; two clones with the same normalized remote merged; equal SHA in distinct configured repositories kept separate; inaccessible workspace warning; and no fetch/mutation commands.
- [ ] Add primary-workspace tests for explicit configuration, main-worktree fallback, lexicographic fallback, missing configured path, and dirty-state independence.
- [ ] Run:

  ```bash
  python3 -m unittest tests.test_commit_stats -v
  ```

  Expected: failures because unique repository history does not exist.

- [ ] Implement history collection by reading each tracked workspace’s `HEAD` history with SHA-bearing `git log --numstat`; union parsed records by SHA. This also handles separate local clones that do not know each other’s branch refs. On conflicting metadata for one SHA, retain deterministic data and emit a warning.
- [ ] Split current-tree data from history in the server response. Compute language/LOC/tree size once from the selected primary workspace per repository; keep branch, dirty, ahead/behind, and each workspace snapshot separate.
- [ ] Produce canonical `projects[] → repositories[] → workspaces[]` stats plus compatibility `repositories` rows during migration. Make portfolio totals derive from canonical repository/project aggregates, never raw workspace-cache sums.
- [ ] Run focused and full tests, then commit:

  ```bash
  python3 -m unittest tests.test_commit_stats tests.test_companion -v
  python3 -m unittest discover -s tests -v
  git add companion/commit_stats.py companion/server.py tests/test_commit_stats.py tests/test_companion.py
  git commit -m "feat: deduplicate repository commit statistics"
  ```

## Task 4: Persist Workspace Evidence Without Caching Canonical Attribution

**Files:**

- Modify: `companion/ai/adapters/antigravity.py`
- Modify: `companion/ai/adapters/claude_code.py`
- Modify: `companion/ai/adapters/codex.py`
- Modify only if the allowlist needs correction: `companion/ai/usage_cache.py:12-84`
- Modify: `tests/test_ai_usage.py`
- Modify: `tests/test_ai_usage_cache.py`

**Interfaces:** Every normalized event retains `cwd` when the source provides it. The usage cache persists source-origin evidence, including `cwd`, but deliberately does not persist `workspace_id`, `repository_id`, `project_id`, display names, confidence, or configuration-derived warnings. Canonical attribution is recomputed from the current identity configuration after cache loading.

- [ ] Add failing adapter tests showing each supported source retains its recorded absolute `cwd` without deriving project membership from a basename.
- [ ] Add cache tests proving `cwd` survives round-trip, configuration-derived identity fields are discarded, parser-version changes reparse affected source files, and forbidden transcript/content/credential fields are discarded.
- [ ] Run:

  ```bash
  python3 -m unittest tests.test_ai_usage tests.test_ai_usage_cache -v
  ```

  Expected: adapter `cwd` assertions fail; any unsafe cache-allowlist assertion fails.

- [ ] Make the smallest adapter changes and increment only affected parser versions. Keep `SCHEMA_VERSION` unchanged unless the physical SQLite schema actually changes.
- [ ] Keep identity configuration out of usage-cache fingerprints: alias changes must reattribute cached events immediately because resolution happens after cache loading. Keep source call/event keys unchanged.
- [ ] Run focused tests and inspect a temporary SQLite database to confirm no prompt/response/tool payload or raw remote is stored.
- [ ] Run the full suite and commit:

  ```bash
  python3 -m unittest tests.test_ai_usage tests.test_ai_usage_cache -v
  python3 -m unittest discover -s tests -v
  git add companion/ai/adapters companion/ai/usage_cache.py tests/test_ai_usage.py tests/test_ai_usage_cache.py
  git commit -m "feat: retain workspace identity in AI usage cache"
  ```

## Task 5: Canonically Aggregate AI Usage in Both Drill-Down Directions

**Files:**

- Modify: `companion/ai/usage.py:127-end`
- Modify: `companion/server.py:1034-1076`
- Modify: `tests/test_ai_usage.py`
- Modify: `tests/test_ai_api.py`

**Interfaces:**

```python
def aggregate_usage(..., identity_resolver: Optional[ProjectIdentityResolver] = None) -> dict: ...
```

Aggregate response version increments and exposes top-level and period-level `by_project`, `by_repository`, and `by_workspace`. Each period project has `by_model`; each period provider/model has `by_project`. Model identity is `(provider, model)`, not model label alone.

- [ ] Write failing tests for the approved examples: `eravnTrans`/`eravntrans` merge; deleted `projectCompanyQ-plan-6-google` and `-plan-7-tasks` map only through explicit aliases; unrelated suffix-like names do not merge; conflicting aliases remain `Unattributed` with warnings.
- [ ] Add parameterized day/week/month/year reconciliation tests. For every token field and `events`, assert parent equals child sums for project → model and model → project, and top-level project/repository/workspace totals each equal global totals.
- [ ] Add tests that identical model labels from different providers remain separate and that identity resolution never changes event deduplication/counts.
- [ ] Run:

  ```bash
  python3 -m unittest tests.test_ai_usage tests.test_ai_api -v
  ```

  Expected: canonical fields and inverse nested groups fail.

- [ ] Resolve each unique `cwd` once per aggregate request, enrich events after provider-call deduplication, and group only by stable IDs. Events without usable paths go to stable `unattributed` buckets.
- [ ] Extend period serialization so the same event feeds both nested directions without double counting. Add attribution summary counts for `git`, `configured`, `path`, and `unattributed`, plus deduplicated warnings.
- [ ] Pass `State.identity_resolver` into `aggregate_usage`; include aggregate version in the response and retain legacy `project` display aliases temporarily.
- [ ] Run focused and full suites, then commit:

  ```bash
  python3 -m unittest tests.test_ai_usage tests.test_ai_api -v
  python3 -m unittest discover -s tests -v
  git add companion/ai/usage.py companion/server.py tests/test_ai_usage.py tests/test_ai_api.py
  git commit -m "feat: attribute AI tokens to canonical projects"
  ```

## Task 6: Render Canonical Repository and AI Hierarchies

**Files:**

- Modify: `dist/app.js:363-520,648-1220,1839-1960`
- Modify: `dist/index.html`
- Modify: `tests/test_companion.py`
- Modify: `tests/test_ai_api.py`

**Interfaces:** UI state uses stable IDs for expansion/filter selection. Repository view renders project → repository → workspace; AI project view renders project → provider/model; AI model view renders provider/model → project.

- [ ] Add failing static UI contract tests for stable-ID data attributes, both inverse nested renderers, attribution-quality callout, workspace status rows, and the absence of pricing/cost labels.
- [ ] Add a fixture/API reconciliation test used by the UI that includes one multi-repository project, two worktrees, one configured historical alias, and one unattributed event.
- [ ] Run:

  ```bash
  python3 -m unittest tests.test_companion tests.test_ai_api -v
  ```

  Expected: new hierarchy markers fail.

- [ ] Replace path-summed `portfolioNumbers()` and `mergedCommitsByDate()` inputs with canonical project/repository totals. Keep workspace branch/dirty/freshness values visible under their repository.
- [ ] Add expand/collapse for project, repository, model, and project-under-model rows. Add model and project filters keyed by IDs, parent-share percentages, and expand/collapse-all scoped to the selected period.
- [ ] Add an attribution callout summarizing configured and unresolved counts; its detail lists workspace display paths and warning codes without transcript content.
- [ ] Verify responsive overflow, keyboard-operable buttons, preserved focus after rerender, and no duplicate DOM IDs.
- [ ] Run:

  ```bash
  node --check dist/app.js
  python3 -m unittest tests.test_companion tests.test_ai_api -v
  python3 -m unittest discover -s tests -v
  ```

- [ ] Commit:

  ```bash
  git add dist/app.js dist/index.html tests/test_companion.py tests/test_ai_api.py
  git commit -m "feat: show canonical project drilldowns"
  ```

## Task 7: Seed Approved Local Mappings and Document Operations

**Files:**

- Modify: `README.md`
- Local-only, do not commit: `.repo-local/project-identities.json`

**Interfaces:** Seed these approved mappings without adding tracked repositories:

- project `eravntrans`, display `eravnTrans`, alias `/Users/longson/projects/eravntrans`;
- project `projectcompanyq`, display `projectCompanyQ`, repository `remote:github.com/longson84/projectcompanyq`, primary workspace `/Users/longson/Projects/projectCompanyQ`, aliases for `projectCompanyQ-plan-6-google` and `projectCompanyQ-plan-7-tasks`.

- [ ] Add a failing round-trip/validation test if the real local remote resolves differently from the proposed normalized repository ID. Correct the local config only from observed Git metadata; do not broaden aliases.
- [ ] Update README with the three-level identity model, exact precedence, config example, warning behavior, cache rebuild behavior, and how to inspect workspace-level attribution.
- [ ] Save the local identity file through the production config writer, confirm mode `0600`, and verify it is ignored/untracked.
- [ ] Run a real local aggregation and assert there is one `eravnTrans` project and one `projectCompanyQ` project containing the two historical workspaces. Record unresolved entries rather than guessing.
- [ ] Run the full suite, then commit documentation only:

  ```bash
  python3 -m unittest discover -s tests -v
  git status --short
  git add README.md
  git commit -m "docs: explain shared project identity"
  ```

## Task 8: Native End-to-End Verification and Final Review

**Files:** No planned source edits. Any discovered defect returns to its owning task with a failing regression test and a separate fix commit.

- [ ] Start the companion with the repository’s documented native command and a temporary/known local port. Do not use a hosted browser or deploy anything.
- [ ] Through authenticated local APIs, verify:
  - identity config loads without exposing remote credentials;
  - tracked selections are unchanged;
  - repository stats show unique commit counts across worktrees;
  - project/repository/workspace totals reconcile;
  - AI day/week/month/year totals reconcile in both nested directions;
  - pricing/cost fields are absent.
- [ ] In the native browser, verify project/model expansion, model/project filters, period navigation, configured/unattributed warnings, workspace branch/dirty rows, focus restoration, and narrow viewport table behavior.
- [ ] Run final automated gates:

  ```bash
  git diff --check
  node --check dist/app.js
  python3 -m unittest discover -s tests -v
  rg -n "estimated_cost|pricing_status|/api/ai/prices|EST\. \$" companion dist README.md
  git status --short
  ```

- [ ] Review against every requirement in the design spec and all five Review Focus items. Confirm each has a named automated test or recorded native acceptance result.
- [ ] Inspect commit history and working tree. Do not claim remote, CI, or deployment verification; this plan proves local main only.

## Completion Criteria

- `eravnTrans` case variants appear as one canonical project without losing workspace evidence.
- `projectCompanyQ` historical worktrees attribute to the configured project without suffix heuristics.
- Shared Git commits are counted once per repository; divergent tracked tips are included.
- Project LOC uses one labeled primary workspace per repository.
- AI token totals reconcile by project, repository, workspace, model, surface, tool, and every period grain.
- Both project → model and model → project views work in the native UI.
- Identity warnings are visible, durable config is private/atomic, caches are rebuildable, and pricing remains absent.
