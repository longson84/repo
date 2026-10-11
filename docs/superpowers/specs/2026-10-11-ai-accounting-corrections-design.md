# AI Local History — Accounting Corrections Design

**Date:** 2026-10-11

**Status:** Implemented and verified locally

**Product:** Theana / Repo local dashboard

**Supersedes:** Identity, usage, surface, pricing, and performance details in `2026-10-11-ai-local-history-design.md`

## Intent

Make local AI history trustworthy enough to answer four distinct questions:

1. How many user-visible conversations exist?
2. How many root and child agent executions ran?
3. How many tokens were reported by unique provider calls?
4. What portion of those tokens can be assigned a defensible retail API cost estimate?

The system must not equate files with sessions, streaming records with provider calls, storage locations with user surfaces, or unknown models with a default price.

## Success criteria

- Claude streaming records for one provider message contribute usage once.
- A top-level conversation counts once even when child-agent transcripts or alternate store representations exist.
- Child-agent usage remains included in token totals and is attributable to its parent conversation.
- Session selection is unambiguous across sources.
- Codex Desktop, terminal, VS Code, exec, and child-agent provenance are represented instead of the combined `cli+app` label.
- Unknown or unmatched models remain unpriced; the API and UI report pricing coverage.
- Codex per-call sums are compared with cumulative counters and disagreements are visible.
- Repeated aggregation reuses normalized events for unchanged files.
- Existing clients continue to receive the current aggregate fields while new identity and quality fields are added.

## Non-goals

- Reconstructing provider invoices or subscription charges
- Guessing missing token counts
- Inferring that similarly titled conversations are identical
- Combining conversations across machines without a provider-issued stable identity
- Parsing Claude Desktop or Cursor IDE usage when no supported usage ledger is available
- Persisting conversation text in the usage cache

## Domain model

### Store

A physical vendor-controlled root or file family. `source_id` continues to identify a store adapter, for example `claude.code` or `cursor.agent-transcripts`.

A store is discovery evidence, not a session and not necessarily a surface. `cursor.agent-transcripts` is a representation of Cursor activity, not a user-facing surface.

### Conversation

A top-level user-visible interaction. Its stable public identifier is:

```text
conversation_key = <tool>:conversation:<provider-conversation-id>
```

When the provider ID is not globally stable within a tool, include `source_id` in the key. Never merge by title, preview, project, timestamp, or path alone.

Conversation summaries expose:

- `conversation_key`
- backward-compatible `id`
- `source_ids` and `paths`
- `tool`, `surface`, `project`, `cwd`
- `started_at`, `updated_at`, `title`, `preview`, `size_bytes`
- `execution_count`
- `identity_confidence`: `provider`, `store-scoped`, or `path-derived`

### Execution

A root run, resumed run, branch, or child/subagent run belonging to a conversation.

```text
execution_key = <source_id>:execution:<provider-execution-id-or-path-id>
```

Executions expose `parent_execution_key`, `kind` (`root`, `child`, `resume`, `branch`, or `unknown`), and provenance. Child executions do not increment the conversation count. Their usage does contribute to totals.

### Usage event

One normalized, unique provider/model call. Every event contains:

- `event_key`
- `conversation_key`
- `execution_key`
- `source_id`, `tool`, `surface`
- `provider`, `model`, optional raw `model_id`
- `project`, `cwd`, `date`, optional full timestamp
- input, cached-input, cache-write, output, thinking, and visible token dimensions when available
- `parser`, `parser_version`, and `confidence`
- optional reconciliation metadata

`event_key` uses a provider request/message ID when available. Otherwise it uses a deterministic store-scoped key based on immutable record identity, not token values or text.

## Adapter rules

### Claude Code

- Top-level `projects/<encoded-project>/<session>.jsonl` files define conversations.
- Files beneath `<session>/subagents/` define child executions of that conversation.
- The parent folder/session ID is authoritative for the conversation; a child row's inherited `sessionId` must not turn the child file into another conversation.
- Assistant JSONL rows sharing `message.id` are streaming snapshots of one provider call. Emit one usage event per message ID, using the terminal record. For monotonic token fields, retain the greatest observed terminal value if malformed ordering is encountered.
- If no message ID exists, use the row UUID when present. If neither exists, use a deterministic file-and-record key and mark confidence `path-derived`.
- A message ID copied into more than one physical file is emitted once within the `claude.code` source. Prefer the record attached to the most specific execution relationship; identical copies never add tokens twice.

### Codex

- Each rollout file is an execution. `parent_thread_id`, `forked_from_id`, `thread_source`, and structured subagent `source` data establish parent/child or branch relationships.
- `session_meta.id`/`session_id` identifies the execution. A top-level rollout defines a conversation; a child rollout uses its reachable top-level ancestor as `conversation_key`.
- Derive surface from metadata, in priority order:
  1. inherit the top-level parent's surface for child agents;
  2. recognized `originator` values (`Codex Desktop`, `codex-tui`, `codex_exec`);
  3. recognized scalar `source` values (`vscode`, `cli`, `exec`);
  4. `unknown`.
- Keep using `last_token_usage` for per-call attribution, separating cached input from total input.
- Track the latest `total_token_usage` per execution. Report the event sum, cumulative total, signed difference, and status: `matched`, `different`, or `unavailable`.
- Do not silently force reconciliation. Resumed or compacted history may legitimately make the two measures cover different scopes.
- A token event before any explicit model context uses model `unknown`, not a hardcoded model.

### Antigravity

- Conversation identity is the provider conversation UUID.
- The same UUID appearing in multiple roots is represented as one conversation only when provider identity proves equivalence; retain all `source_ids` and paths.
- Usage events remain sourced from model steps in conversation databases. Deduplicate only when the same conversation step identity is present in more than one source.
- Preserve raw model enum IDs. Known enums receive a normalized model name; unknown enums remain named `antigravity-model-<id>` and unpriced unless configured explicitly.
- Validate the observed `thinking + visible = output` invariant when both components exist and surface violations in quality metadata.

### Cursor

- Cursor CLI `store.db` is the primary conversation representation.
- Agent transcript files with the same provider agent/conversation UUID are aliases or execution detail for that conversation, not another surface.
- Transcript-only UUIDs still create conversations with store-scoped or path-derived confidence.
- `agent-transcripts` must never appear as a user-facing surface. Use the surface established by the primary store or `unknown`.
- Cursor remains usage-unsupported until structured counters are demonstrated.

## Detection and counts

Detect continues to list every physical source, including aliases, but its summary separates:

- `stores_found`
- `conversation_estimate`
- `execution_estimate`
- `representation_estimate`

Per-source records retain raw store counts in `details`. A state database's existence is not one session; unknown counts are `null`, never `1` merely because a database exists.

Logical counts come from normalized identities when adapters support them. Approximate raw counts must be labeled as estimates and must not be summed across overlapping representations as though they were disjoint.

## Session API

`POST /api/ai/sessions` continues to accept source, project, and limit filters. It returns one row per conversation plus optional execution summaries.

New request fields:

```json
{
  "include_executions": false,
  "conversation_keys": []
}
```

New response fields:

```json
{
  "conversation_count": 0,
  "execution_count": 0,
  "representation_count": 0,
  "sessions": []
}
```

`count` remains a backward-compatible alias for `conversation_count`.

## Usage API and aggregation

`POST /api/ai/usage` prefers `conversation_keys` and may accept compound selectors:

```json
{
  "sessions": [{"source_id": "claude.code", "session_id": "..."}]
}
```

Legacy `session_ids` remains accepted only when every requested ID resolves unambiguously in the selected sources. Ambiguous IDs return HTTP 400 with candidate canonical keys.

Aggregation consumes normalized events and retains the existing totals and `by_*` arrays. It adds:

- `conversation_count`
- `execution_count`
- `event_count`
- `quality` with parser warnings and reconciliation counts
- `pricing_coverage`
- `reconciliation`

Model grouping uses `(provider, model)` internally. Rows expose both fields so identical model labels from different providers do not merge accidentally.

## Pricing

### Resolution

Rates must be selected by explicit exact model key or explicit configured alias. Implicit longest-prefix matching and the generic default price are removed from automatic cost totals.

Each rate entry may include:

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "effective_from": "2026-01-01",
  "effective_to": null,
  "input": 3.0,
  "cached": 0.3,
  "output": 15.0,
  "cache_write": 3.75
}
```

Legacy `per_million` entries are loaded as undated exact matches for compatibility. They are not prefix patterns. Users may define explicit aliases separately.

### Coverage

Every event has pricing status `priced`, `unpriced_model`, `outside_rate_period`, or `invalid_rate`. Unpriced events contribute tokens and event counts but contribute no value to `cost_usd`.

The aggregate exposes priced and unpriced event/token counts plus coverage percentages. The UI labels cost as partial whenever coverage is below 100%.

No unknown model receives a fabricated default rate.

## Incremental usage cache

Use stdlib SQLite at `.repo-local/ai/usage.db`. The cache contains normalized metadata and token counters only; it never stores transcript text.

Each parsed file is keyed by:

- absolute path
- source ID
- size
- nanosecond modification time
- parser name and version

On aggregation:

1. discover candidate usage files;
2. reuse events for unchanged fingerprints;
3. reparse changed or new files inside one transaction;
4. remove cached rows for files no longer present in that source;
5. aggregate from the resulting normalized events.

If the cache is unavailable or corrupt, rename it to a timestamped recovery file when possible and rebuild. Failure to cache must not prevent a direct read-only aggregation; the response reports cache status.

The companion serializes cache refreshes with its existing state lock or a dedicated AI-cache lock. SQLite uses bounded busy timeouts. Vendor files remain read-only.

## UI behavior

- Detect labels physical stores and shows separate logical conversation and execution totals when available.
- Sessions displays one row per conversation and an execution count. Child executions are expandable detail, not peers in the default list.
- Tokens labels the count as unique usage events.
- Surface rows use actual user surfaces; storage representations are visible only in provenance detail.
- Cost shows priced coverage and uses “Partial estimate” when coverage is incomplete.
- Quality warnings summarize unknown models, identity uncertainty, parser invariant violations, and Codex reconciliation differences.
- Existing source/project/time grouping remains available.

## Security and privacy

- Preserve loopback binding, origin allow-listing, and bearer-token authorization.
- Never mutate vendor stores.
- Cache only normalized identifiers, provenance, timestamps, token counters, and quality metadata—never prompts, responses, tool payloads, or snippets.
- Continue placing all application-owned AI state under `.repo-local/ai/` with owner-only permissions where supported.
- API paths remain limited to catalogued roots; no arbitrary read endpoint is introduced.

## Compatibility and migration

- Existing `prices.json` remains readable. The first write upgrades it without discarding user-defined exact rates.
- Existing Detect cache is versioned; an older payload is ignored or regenerated.
- Existing session fields remain where their meaning is preserved. `id` remains the vendor-facing ID; new consumers use `conversation_key`.
- Existing aggregate token fields remain. Cost may decrease because duplicate calls and invented default prices are removed; the UI explains the corrected accounting semantics.
- `usage.db` is rebuildable derived state and may be deleted safely while the companion is stopped.

## Error handling

- Malformed individual log rows are skipped and counted in quality warnings.
- Unsupported schema versions or missing required tables mark a source degraded instead of producing zero silently.
- Ambiguous legacy session selectors return validation errors.
- Cache corruption triggers rebuild/fallback and a visible warning.
- Unknown surfaces, models, dates, and projects remain explicit `unknown` values rather than guessed defaults.

## Testing

All fixtures use temporary homes and synthetic stores. Required regression coverage:

1. Repeated Claude rows with one `message.id` emit one event and terminal token counters.
2. Claude subagent files increase execution count and usage but not conversation count.
3. A copied Claude message ID across files contributes once.
4. Cursor CLI and agent transcript representations merge by proven UUID.
5. Same bare session ID across sources is rejected as ambiguous.
6. Codex surface is derived from Desktop, terminal, VS Code, and exec metadata; children inherit the root surface.
7. Codex usage before model context remains `unknown`.
8. Codex matched, different, and unavailable reconciliation states are reported.
9. Unknown Antigravity enums retain tokens and remain unpriced.
10. Unknown models never use a default rate.
11. Effective-dated rates select the correct period and expose uncovered dates.
12. Cache reuse avoids reparsing unchanged files; changes, deletion, and parser-version increments invalidate correctly.
13. Corrupt cache falls back safely without losing aggregation.
14. Detect separates physical representation counts from logical conversation counts.
15. Existing API fields remain compatible where specified.

Run the complete Python test suite, JavaScript formatting check, and Python formatting/lint checks used by the repository. API tests must run with loopback access.

## Delivery order

1. Normalize identity and Claude usage deduplication.
2. Correct sessions and compound selectors across adapters.
3. Add Codex surface and reconciliation semantics.
4. Replace pricing fallback with explicit, dated resolution and coverage.
5. Add the incremental usage cache.
6. Update Detect, APIs, and UI presentation.
7. Run full regression and live-data invariants without exposing conversation content.

Each step must leave fixture tests green. The final live-data check compares counts and reconciliation summaries only; it must not snapshot the user's private transcripts into tests or documentation.

## Approval record

- The user requested all findings from the 2026-10-11 review be fixed.
- The user approved this design direction and the rule that child-agent usage belongs in token totals while child agents do not count as separate user sessions.

## Verification record

Implemented on `main` on 2026-10-11. The complete Python suite passed 82 tests. The UI passed the pinned Prettier check, all changed Python files passed Ruff formatting, and privacy-safe live checks confirmed Claude duplicate collapse, child execution exclusion from conversation counts, normalized surfaces, unknown-model non-pricing, Codex reconciliation reporting, and second-scan cache reuse with zero misses or reparses. The repository-wide Ruff lint command also exposed pre-existing modernization/style rules outside this feature scope; implementation-critical unused-code checks were addressed separately.
