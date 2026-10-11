# AI Accounting Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce trustworthy local AI conversation counts, unique token usage, surface attribution, reconciliation, pricing coverage, and fast repeat aggregation.

**Architecture:** Vendor adapters normalize physical records into conversation, execution, and usage-event dictionaries with canonical keys. Session and usage services merge those normalized records, price only explicitly matched models, and persist rebuildable usage metadata in a local SQLite cache keyed by source-file fingerprints.

**Tech Stack:** Python 3 stdlib (`json`, `pathlib`, `sqlite3`, `hashlib`, `datetime`), `unittest`, existing vanilla JavaScript UI, existing loopback HTTP companion.

**Spec:** `docs/superpowers/specs/2026-10-11-ai-accounting-corrections-design.md`

## Global Constraints

- Use stdlib only; add no runtime package dependency.
- Never modify vendor AI stores or persist transcript text in application-owned state.
- Keep application state under `.repo-local/ai/` and owner-only where supported.
- Preserve loopback binding, origin allow-listing, bearer authorization, and catalogued-root restrictions.
- Preserve existing API fields when their meaning remains valid; `count` stays an alias for conversation count.
- Count child-agent usage in token totals but not as separate user conversations.
- Unknown identity, surface, model, date, and pricing state stays explicit rather than guessed.
- Use temporary synthetic homes in tests; never copy real conversation content into fixtures.
- Follow strict red-green-refactor: each production behavior starts with a focused failing test.

## File structure

- Create `companion/ai/identity.py`: canonical keys, execution metadata, and deterministic conversation merging.
- Create `companion/ai/usage_cache.py`: usage-file fingerprints, SQLite schema, cache refresh, and fallback.
- Modify `companion/ai/adapters/*.py`: emit normalized conversations, executions, events, provenance, and quality metadata.
- Modify `companion/ai/sessions.py`: merge representations and apply canonical selectors.
- Modify `companion/ai/usage.py`: deduplicate events, resolve selectors, aggregate quality/reconciliation/pricing coverage, and use cache.
- Modify `companion/ai/prices.py`: exact/effective-dated price resolution and legacy-file migration.
- Modify `companion/ai/detect.py`: separate store, representation, conversation, and execution estimates.
- Modify `companion/server.py`: validate the extended session/usage contracts and provide the cache path/lock.
- Modify `dist/app.js` and `dist/index.html`: display corrected counts, surfaces, coverage, cache, and quality state.
- Extend the existing `tests/test_ai_*.py` files and add `tests/test_ai_usage_cache.py`.

## Review Focus

- Two different tools reuse the same vendor ID: compound identity must keep them separate, and a bare legacy selector must fail as ambiguous (Task 2).
- A log is appended without a reliable nanosecond timestamp change: the cache fingerprint includes size as well as `mtime_ns`, and the changed file is reparsed (Task 5).
- An effective-dated price has adjacent date boundaries: both inclusive endpoints and the first uncovered day are tested (Task 4).
- A Codex child appears before its parent file during discovery: surface and conversation ancestry resolve after the complete metadata graph is built (Task 3).
- A malformed row follows valid rows in an active file: valid events survive, the malformed row increments quality warnings, and cache refresh remains atomic (Tasks 1 and 5).

---

### Task 1: Canonical identity and Claude deduplication

**Files:**
- Create: `companion/ai/identity.py`
- Modify: `companion/ai/adapters/base.py`
- Modify: `companion/ai/adapters/claude_code.py`
- Test: `tests/test_ai_sessions.py`
- Test: `tests/test_ai_usage.py`

**Interfaces:**
- Produces `conversation_key(tool: str, provider_id: str, *, source_id: str | None = None, confidence: str = "provider") -> str`.
- Produces `execution_key(source_id: str, execution_id: str) -> str`.
- Produces normalized session rows containing `conversation_key`, `source_ids`, `paths`, `execution_count`, `executions`, and `identity_confidence`.
- Produces Claude usage events containing `event_key`, `conversation_key`, `execution_key`, `provider`, `parser`, `parser_version`, and `confidence` while retaining existing token fields.
- Produces `claude_code.collect_usage(root: Path, source_id: str = "claude.code") -> {events, reconciliation, warnings}`.
- Keeps `claude_code.list_sessions()` and `claude_code.iter_usage()` as compatibility entry points.

- [ ] **Step 1: Add failing Claude identity and streaming tests**

Add tests named:

- `test_claude_subagents_are_executions_not_conversations`
- `test_claude_repeated_message_rows_emit_terminal_usage_once`
- `test_claude_copied_message_id_is_counted_once_across_files`
- `test_claude_malformed_row_preserves_valid_usage_and_reports_warning`

Use literal fixtures where one root transcript and two `subagents/*.jsonl` files share the parent `sessionId`. Repeated rows must share `message.id`, repeat input/cache counts, and end with the largest output count. Assert one conversation, three executions, and one usage event for each unique message ID.

- [ ] **Step 2: Run the focused tests and verify the expected failures**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_sessions tests.test_ai_usage -v`

Expected: the new tests fail because subagent files are returned as sessions and repeated assistant rows are summed.

- [ ] **Step 3: Add canonical key helpers and normalized base fields**

Implement the two key functions in `identity.py`. Extend `adapters.base.summary()` with optional normalized identity/execution fields without removing current keys. Provider-confidence keys omit `source_id`; store/path-derived keys include it.

- [ ] **Step 4: Correct Claude conversation and execution discovery**

Change `claude_code.list_sessions()` to group a top-level transcript and its `subagents/` files into one conversation row. Child execution IDs come from the child filename or row UUID, while the conversation ID comes from the parent transcript/folder. Adapters always return internal `executions`; `sessions.list_sessions()` removes that array unless `include_executions` is true.

- [ ] **Step 5: Correct Claude usage event normalization**

Parse all Claude usage rows into an ordered map keyed by `message.id`, then row UUID, then deterministic file/record position. For repeated snapshots, retain the last record while taking the maximum observed value of monotonic token dimensions. Deduplicate the same provider message ID across files and return parser warnings separately through `claude_code.collect_usage()`; keep `iter_usage()` as an event-only wrapper.

- [ ] **Step 6: Run focused and existing adapter tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_sessions tests.test_ai_usage -v`

Expected: all session and usage tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add companion/ai/identity.py companion/ai/adapters/base.py companion/ai/adapters/claude_code.py tests/test_ai_sessions.py tests/test_ai_usage.py
git commit -m "fix: normalize Claude sessions and token events"
```

### Task 2: Merge logical conversations and enforce compound selectors

**Files:**
- Modify: `companion/ai/adapters/antigravity.py`
- Modify: `companion/ai/adapters/cursor.py`
- Modify: `companion/ai/sessions.py`
- Modify: `companion/ai/usage.py`
- Modify: `companion/server.py`
- Test: `tests/test_ai_sessions.py`
- Test: `tests/test_ai_usage.py`
- Test: `tests/test_ai_api.py`

**Interfaces:**
- Consumes canonical helpers and normalized rows from Task 1.
- Produces `collect_sessions(*, sources, project, env, home) -> list[dict]` before sorting/limiting.
- Produces `resolve_conversation_selectors(rows, *, conversation_keys, compound_sessions, legacy_session_ids) -> set[str] | None`.
- Extends `list_sessions()` with `include_executions: bool = False` and `conversation_keys: Sequence[str] | None = None`.
- Extends `aggregate_usage()` with `conversation_keys` and `compound_sessions` while retaining guarded legacy `session_ids`.

- [ ] **Step 1: Add failing cross-store identity and selector tests**

Add tests named:

- `test_cursor_cli_and_transcript_uuid_merge_into_one_conversation`
- `test_antigravity_same_provider_uuid_merges_sources_without_duplicate_session`
- `test_antigravity_duplicate_step_identity_is_counted_once_across_sources`
- `test_antigravity_unknown_enum_preserves_model_id_and_reports_output_invariant_warning`
- `test_same_bare_id_across_tools_requires_compound_selector`
- `test_session_response_reports_conversation_execution_and_representation_counts`
- `test_usage_api_accepts_conversation_keys_and_compound_sessions`

Assert that `cursor.agent-transcripts` is provenance, not a returned surface, and that a legacy ambiguous ID raises `ValueError` listing candidate conversation keys.

- [ ] **Step 2: Run the focused tests and verify failures**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_sessions tests.test_ai_usage tests.test_ai_api -v`

Expected: failures show duplicate rows, missing canonical selectors/counts, and acceptance of ambiguous bare IDs.

- [ ] **Step 3: Normalize Antigravity and Cursor conversation rows**

Add canonical provider conversation keys to both adapters. Cursor transcript rows with a UUID matching a CLI agent ID merge into that conversation and add path/source/execution provenance; transcript-only rows remain `unknown` surface with store/path confidence. Give Antigravity model steps deterministic step-based event keys, preserve raw enum IDs, and add an invariant warning when non-zero thinking plus visible tokens differs from output.

- [ ] **Step 4: Merge conversations in the session service**

Implement `collect_sessions()` and deterministic merging by `conversation_key`: union source IDs and paths, sum distinct executions, prefer provider-confidence metadata, use the earliest start/latest update, and never merge on title/project/time alone. Apply project filtering, sorting, and limit only after merging.

- [ ] **Step 5: Add canonical and compatibility selectors**

Resolve `conversation_keys` directly, compound selectors by `(source_id, session_id)`, and legacy IDs only after inventory proves a single candidate. Make `sessions.py`, `usage.py`, and server validation use the same resolver and error contract.

- [ ] **Step 6: Run focused and API tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_sessions tests.test_ai_usage tests.test_ai_api -v`

Expected: all tests pass, including API compatibility assertions for `count`.

- [ ] **Step 7: Commit Task 2**

```bash
git add companion/ai/adapters/antigravity.py companion/ai/adapters/cursor.py companion/ai/sessions.py companion/ai/usage.py companion/server.py tests/test_ai_sessions.py tests/test_ai_usage.py tests/test_ai_api.py
git commit -m "fix: merge AI conversations by canonical identity"
```

### Task 3: Derive Codex surfaces and expose reconciliation

**Files:**
- Modify: `companion/ai/adapters/codex.py`
- Modify: `companion/ai/usage.py`
- Test: `tests/test_ai_sessions.py`
- Test: `tests/test_ai_usage.py`

**Interfaces:**
- Consumes canonical identity from Task 1 and merging/selectors from Task 2.
- Produces `codex.collect_metadata(root: Path, source_id: str = "codex.home") -> dict[str, dict]` for a complete parent graph.
- Produces `codex.surface_for(meta: Mapping, all_meta: Mapping[str, Mapping]) -> str` with child inheritance.
- Produces `codex.collect_usage(...) -> {events, reconciliation, warnings}`.
- Adds aggregate `reconciliation` and reconciliation counts under `quality`.

- [ ] **Step 1: Add failing Codex provenance tests**

Add table-driven fixtures and tests named:

- `test_codex_surfaces_come_from_desktop_tui_vscode_and_exec_metadata`
- `test_codex_child_discovered_before_parent_inherits_parent_surface`
- `test_codex_usage_before_turn_context_has_unknown_model`
- `test_codex_reconciliation_reports_matched_different_and_unavailable`

Use literal session metadata and token counters. Assert child usage remains separate events but shares the root conversation key and surface.

- [ ] **Step 2: Run focused tests and verify failures**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_sessions tests.test_ai_usage -v`

Expected: Codex is still `cli+app`, early usage is `gpt-5.5`, and no reconciliation result exists.

- [ ] **Step 3: Build Codex metadata graph before emitting rows**

Parse every rollout's `session_meta` first, retain parent/thread/originator/source fields, resolve the reachable top-level ancestor independent of filesystem order, and guard cycles/missing parents with `unknown` ancestry plus warnings.

- [ ] **Step 4: Derive normalized conversations, executions, and surfaces**

Map recognized metadata to `desktop`, `terminal`, `vscode`, and `exec`; child agents inherit their resolved root surface. Replace the hardcoded `cli+app` value in session and usage rows. Initialize model to `unknown` until a `turn_context` supplies it.

- [ ] **Step 5: Add Codex accounting reconciliation**

For each execution, compare summed emitted `last_token_usage` dimensions with the latest cumulative total and emit signed per-dimension differences plus `matched`, `different`, or `unavailable`. Do not alter token totals to force a match.

- [ ] **Step 6: Run focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_sessions tests.test_ai_usage -v`

Expected: all Codex provenance and reconciliation tests pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add companion/ai/adapters/codex.py companion/ai/usage.py tests/test_ai_sessions.py tests/test_ai_usage.py
git commit -m "fix: attribute Codex surfaces and reconcile usage"
```

### Task 4: Make pricing exact, dated, and coverage-aware

**Files:**
- Modify: `companion/ai/prices.py`
- Modify: `companion/ai/usage.py`
- Test: `tests/test_ai_prices.py`
- Test: `tests/test_ai_usage.py`

**Interfaces:**
- Produces `resolve_rate(*, provider: str, model: str, event_date: str | None, table: Mapping) -> tuple[Mapping[str, float] | None, str]`.
- Produces `price_usage(event: Mapping, table: Mapping) -> {cost_usd, status, matched_rate}`.
- Keeps `estimate_cost()` only as a compatibility wrapper returning the priced cost or `0.0`; aggregation must use `price_usage()`.
- Adds `pricing_coverage` with priced/unpriced events and token totals plus event/token percentages.

- [ ] **Step 1: Add failing exact-rate and coverage tests**

Add tests named:

- `test_unknown_model_is_unpriced_instead_of_using_default`
- `test_model_prefix_does_not_match_without_explicit_alias`
- `test_explicit_alias_resolves_to_exact_rate`
- `test_effective_rate_boundaries_are_inclusive`
- `test_first_day_outside_rate_period_is_unpriced`
- `test_legacy_per_million_loads_as_undated_exact_rates`
- `test_aggregate_reports_partial_pricing_coverage`
- `test_same_model_label_from_different_providers_has_separate_rows`

Use hand-calculated token/cost literals and assert unknown events retain their tokens while adding zero to cost.

- [ ] **Step 2: Run price and aggregate tests and verify failures**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_prices tests.test_ai_usage -v`

Expected: unknown/prefix models currently receive default or family rates and no coverage exists.

- [ ] **Step 3: Implement versioned exact and dated price resolution**

Support `rates` plus explicit `aliases`; treat legacy `per_million` keys as exact undated entries. Ignore `default` for automatic matching. Validate numeric non-negative rates and inclusive ISO date bounds, returning `invalid_rate` or `outside_rate_period` without throwing away usage.

- [ ] **Step 4: Aggregate pricing coverage**

Replace direct `estimate_cost()` calls with `price_usage()`. Count priced/unpriced events and input/cache/output/cache-write tokens, calculate percentages with an explicit zero-denominator rule, and add model-level pricing status. Key model aggregation by `(provider, model)` and expose both values on each row.

- [ ] **Step 5: Preserve and upgrade user price files**

Make `load_prices`, `save_prices`, and `update_prices` retain existing exact user rates and safely write the versioned schema on the next update. Set owner-only permissions on the atomic replacement file.

- [ ] **Step 6: Run focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_prices tests.test_ai_usage -v`

Expected: all pricing and coverage tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add companion/ai/prices.py companion/ai/usage.py tests/test_ai_prices.py tests/test_ai_usage.py
git commit -m "fix: price only explicitly matched AI models"
```

### Task 5: Add incremental normalized-usage cache

**Files:**
- Create: `companion/ai/usage_cache.py`
- Modify: `companion/ai/adapters/antigravity.py`
- Modify: `companion/ai/adapters/claude_code.py`
- Modify: `companion/ai/adapters/codex.py`
- Modify: `companion/ai/usage.py`
- Modify: `companion/server.py`
- Create: `tests/test_ai_usage_cache.py`
- Modify: `tests/test_ai_api.py`

**Interfaces:**
- Produces adapter `usage_files(...) -> Iterable[dict]` descriptors and `parse_usage_file(descriptor) -> {events, reconciliation, warnings}`.
- Produces `refresh_usage_cache(cache_path: Path, descriptors: Iterable[dict]) -> {events, reconciliation, warnings, cache}`.
- Extends `aggregate_usage(..., cache_path: Path | None = None)`; `None` remains direct parsing for simple callers/tests.
- Server passes `.repo-local/ai/usage.db` and serializes refreshes with `ai_cache_lock`.

- [ ] **Step 1: Add failing cache behavior tests**

Add tests named:

- `test_unchanged_fingerprint_reuses_cached_events_without_reparse`
- `test_size_change_invalidates_when_mtime_ns_is_unchanged`
- `test_deleted_file_removes_cached_events`
- `test_parser_version_change_invalidates_cached_events`
- `test_malformed_changed_file_keeps_refresh_atomic`
- `test_corrupt_cache_falls_back_to_direct_aggregation_and_reports_warning`
- `test_cache_never_persists_transcript_text`

Use a real temporary SQLite file and real parser callback; avoid mocking SQLite. A counting parser double may count parse invocations, but assertions must target returned events/cache status and database content. If a parser raises, assert that the previous committed rows remain intact and the response falls back to direct parsing with a warning.

- [ ] **Step 2: Run cache tests and verify failures**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_usage_cache -v`

Expected: import or missing-interface failures because the cache does not exist.

- [ ] **Step 3: Implement schema, permissions, and fingerprints**

Create versioned `files`, `events`, `reconciliation`, and `warnings` tables containing normalized metadata only. Fingerprints include source, absolute path, size, `mtime_ns`, parser name/version, and declared dependency fingerprints. Configure bounded busy timeout and atomic per-refresh transactions.

- [ ] **Step 4: Adapt usage parsers to file descriptors**

Split each adapter into discovery and per-file parsing while retaining `collect_usage()`/`iter_usage()` wrappers. Claude global message deduplication occurs after cached events are combined. Codex ancestry/surface resolution occurs after cached execution metadata is combined.

- [ ] **Step 5: Implement invalidation, deletion, and safe fallback**

Reuse exact fingerprints, reparse changed descriptors, delete absent-file rows within the same source refresh, and invalidate on parser-version changes. On corrupt/unavailable cache, attempt a timestamped recovery rename, parse directly, and return `cache.status = "bypassed"` with a warning.

- [ ] **Step 6: Wire the server cache path and lock**

Initialize `ai_usage_cache_path` and a dedicated lock in `State`; pass the path to aggregation and hold the lock only for refresh/database work. Keep the endpoint response synchronous and include cache hit/miss/reparse counters.

- [ ] **Step 7: Run cache and API tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_usage_cache tests.test_ai_api tests.test_ai_usage -v`

Expected: all tests pass, and a second fixture aggregation reports cache hits without reparsing.

- [ ] **Step 8: Commit Task 5**

```bash
git add companion/ai/usage_cache.py companion/ai/adapters/antigravity.py companion/ai/adapters/claude_code.py companion/ai/adapters/codex.py companion/ai/usage.py companion/server.py tests/test_ai_usage_cache.py tests/test_ai_api.py
git commit -m "feat: cache normalized AI usage incrementally"
```

### Task 6: Correct Detect semantics and update the UI

**Files:**
- Modify: `companion/ai/detect.py`
- Modify: `companion/ai/sessions.py`
- Modify: `companion/server.py`
- Modify: `dist/app.js`
- Modify: `dist/index.html`
- Modify: `tests/test_ai_detect.py`
- Modify: `tests/test_ai_api.py`

**Interfaces:**
- Extends Detect summary with `stores_found`, `conversation_estimate`, `execution_estimate`, and `representation_estimate`.
- Keeps `summary.found` and `summary.sessions` only as compatibility aliases, with `sessions` equal to logical conversation estimate when available.
- UI consumes `conversation_count`, `execution_count`, `pricing_coverage`, `quality`, `reconciliation`, and `cache`.

- [ ] **Step 1: Add failing Detect/API contract tests**

Add tests named:

- `test_detect_separates_stores_representations_conversations_and_executions`
- `test_cursor_ide_database_presence_is_not_one_session`
- `test_detect_does_not_sum_overlapping_cursor_representations`
- `test_api_exposes_pricing_quality_reconciliation_and_cache_metadata`

Use a Cursor CLI/transcript UUID overlap and Claude root/child fixture with literal expected counts.

- [ ] **Step 2: Run Detect and API tests and verify failures**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_detect tests.test_ai_api -v`

Expected: current Detect sums raw representations and reports Cursor IDE database presence as one session.

- [ ] **Step 3: Correct Detect details and logical summary**

Return structured raw details per source. Use the normalized session inventory for logical counts where supported, keep unsupported counts `null`, and avoid cross-representation addition. Bump Detect payload version and ignore incompatible cached payloads.

- [ ] **Step 4: Update sessions and token UI copy/data binding**

Show logical conversations and executions separately; label token events as unique provider/model calls. Replace `cli+app`/`agent-transcripts` presentation with normalized surfaces. Show pricing percentage, “Partial estimate” below 100%, cache status, unknown models, parser warnings, and reconciliation differences without exposing transcript content.

- [ ] **Step 5: Preserve empty/loading/error states**

Ensure old cached payloads, absent new fields, unsupported usage sources, an all-unpriced result, and a cache-bypassed response render explanatory fallbacks rather than zeros or JavaScript errors.

- [ ] **Step 6: Run focused tests and formatting**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_ai_detect tests.test_ai_api -v`

Run: `npx --yes prettier@3.6.2 --check dist/index.html dist/app.js`

Run: `uvx ruff format --check companion/ai companion/server.py tests`

Expected: tests pass and both format checks exit 0.

- [ ] **Step 7: Commit Task 6**

```bash
git add companion/ai/detect.py companion/ai/sessions.py companion/server.py dist/app.js dist/index.html tests/test_ai_detect.py tests/test_ai_api.py
git commit -m "fix: present trustworthy AI history metrics"
```

### Task 7: Full regression, live invariants, and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-11-ai-local-history-design.md`
- Modify: `docs/superpowers/specs/2026-10-11-ai-accounting-corrections-design.md`
- Test: all `tests/test_*.py`

**Interfaces:**
- Consumes all preceding task interfaces.
- Produces documented corrected semantics, migration notes, and reproducible verification evidence.

- [ ] **Step 1: Update user-facing documentation**

Document conversations versus executions, unique usage-event accounting, partial pricing coverage, Codex reconciliation, cache location/rebuild behavior, and the expected one-time first-scan cost. Mark the correction spec implemented only after all verification steps pass.

- [ ] **Step 2: Run the complete Python suite with loopback access**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`

Expected: all tests pass with zero errors/failures. If the sandbox blocks loopback binding, rerun the identical command with approved escalation rather than omitting API tests.

- [ ] **Step 3: Run repository formatting and static checks**

Run: `npx --yes prettier@3.6.2 --check dist/index.html dist/app.js`

Run: `uvx ruff format --check companion tests`

Run: `uvx ruff check companion tests`

Expected: every command exits 0.

- [ ] **Step 4: Run privacy-safe live invariants twice**

Run the repository's AI inventory/aggregate functions against the current home while printing counts, IDs only as collision counts, token dimensions, pricing coverage, reconciliation counts, surfaces, cache hit/miss counts, and elapsed time—never titles, previews, prompts, responses, paths beneath session roots, or tool payloads.

Assert:

- Claude unique usage events are fewer than raw non-zero assistant rows when streaming duplicates exist.
- Conversation counts exclude known child executions.
- no returned surface is `cli+app` or `agent-transcripts`;
- unknown models are unpriced;
- the second aggregation reports cache hits and no unnecessary reparses;
- any Codex cumulative disagreement appears in reconciliation rather than changing token totals.

- [ ] **Step 5: Inspect final diff and compatibility checklist**

Run: `git diff --check`

Run: `git status --short`

Review every success criterion and required regression case in the spec against code/tests. Confirm no vendor files, `.repo-local` derived data, private logs, or transcript text entered Git.

- [ ] **Step 6: Commit documentation and final integration**

```bash
git add README.md docs/superpowers/specs/2026-10-11-ai-local-history-design.md docs/superpowers/specs/2026-10-11-ai-accounting-corrections-design.md
git commit -m "docs: explain corrected AI accounting semantics"
```

- [ ] **Step 7: Run final verification after the last commit**

Repeat the complete Python suite, Prettier check, Ruff format check, Ruff lint, `git diff --check HEAD^`, and `git status --short`. Report exact pass counts and any environmental limitation; do not claim deployed/browser behavior unless separately exercised.
