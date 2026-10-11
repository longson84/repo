# AI Local History — Design Spec

**Date:** 2026-10-11  
**Status:** Implemented foundation; accounting semantics superseded by `2026-10-11-ai-accounting-corrections-design.md`
**Product:** Theana / Repo local dashboard

## Intent

Add an **AI** area to the local Repo dashboard so the user can inventory, measure, search, and export AI coding conversations stored on **this machine**. Same trust model as repositories: loopback-only companion, nothing uploaded, data under `.repo-local/`.

Success looks like:

1. One Detect pass shows every relevant local store and separates representations, conversations, and executions.
2. Logical conversations from well-understood tools can be listed, unique token usage/cost estimated, searched, and exported.
3. Claude Desktop and Cursor IDE appear in Detect immediately; deeper parsing follows after CLI-style adapters.

## Non-goals

- Uploading chats or sending content off-machine
- Provider billing APIs / invoice reconciliation
- Deleting or mutating vendor session stores
- Full fidelity for cloud-only Claude.ai history beyond whatever is cached locally
- Replacing Antigravity / Claude / Codex / Cursor UIs

## Sources

### First-class in Detect (phase 1)

| ID | Tool | Surface | Default path(s) |
|----|------|---------|-----------------|
| `antigravity.app` | Antigravity | App | `~/.gemini/antigravity` |
| `antigravity.ide` | Antigravity | IDE | `~/.gemini/antigravity-ide` |
| `antigravity.cli` | Antigravity | CLI (`agy`) | `~/.gemini/antigravity-cli` |
| `claude.code` | Claude Code | CLI | `~/.claude` (`projects/**/*.jsonl`) |
| `claude.desktop` | Claude Desktop | App | `~/Library/Application Support/Claude` |
| `codex.home` | Codex | CLI + App (shared) | `~/.codex` (`sessions/**/rollout-*.jsonl`) |
| `codex.app-support` | Codex | App shell | `~/Library/Application Support/Codex` |
| `cursor.cli` | Cursor | CLI | `~/.cursor/chats` |
| `cursor.ide` | Cursor | IDE | `~/Library/Application Support/Cursor` (+ `…/globalStorage/state.vscdb`) |
| `cursor.agent-transcripts` | Cursor | Agent logs | `~/.cursor/projects/**/agent-transcripts` |

Env overrides (when set, replace home defaults):

- `CLAUDE_CONFIG_DIR` → Claude Code root  
- `CODEX_HOME` → Codex root  

Optional later (Detect may note if present, not required for v1): Copilot, Hermes, Kimi, OpenCode, Aider.

### Depth by phase

| Capability | Antigravity ×3 | Claude Code | Codex | Cursor CLI | Claude Desktop | Cursor IDE |
|------------|----------------|-------------|-------|------------|----------------|------------|
| Detect | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| List sessions | ✓ wave 1 | ✓ wave 1 | ✓ wave 1 | ✓ wave 1 | wave 2 | wave 2 |
| Tokens / cost | ✓ wave 1 (known) | ✓ wave 1 | ✓ wave 1 | wave 1 if fields exist | wave 2 | wave 2 |
| Search | ✓ wave 1 | ✓ wave 1 | ✓ wave 1 | ✓ wave 1 | wave 2 | wave 2 |
| Export | ✓ wave 1 | ✓ wave 1 | ✓ wave 1 | ✓ wave 1 | wave 2 | wave 2 |

**Wave 1** = four CLI-style families (Antigravity surfaces count as one family with three roots).  
**Wave 2** = Claude Desktop + Cursor IDE parsers after wave 1 is solid.

## Architecture

```
UI (dist/)  --JSON-->  Companion (companion/server.py)
                              |
                              +-- ai/catalog.py     known sources + env overrides
                              +-- ai/detect.py      stores, representations, conversations, executions
                              +-- ai/adapters/      per-tool list / usage / text / export
                              +-- ai/prices.py      exact/dated rates + coverage-aware estimate
                              +-- ai/usage_cache.py normalized incremental usage cache
                              +-- ai/search.py      transcript search
                              +-- ai/export.py      write exports under .repo-local
                              |
                         .repo-local/ai/
                              detect.json
                              prices.json
                              usage.db
                              search.db          (optional, phase 4+)
                              exports/
```

Keep adapter modules small. Prefer stdlib only (match existing companion: no pip deps). Use `sqlite3`, `json`, `pathlib`, subprocess `rg` when available for search with a Python fallback.

### Adapter interface (conceptual)

Each adapter exposes:

- `source_id`, `tool`, `surface`
- `detect() -> { exists, path, size_bytes, session_estimate, notes }`
- `list_sessions(limit, project_filter) -> [SessionSummary]`
- `usage_for_session(id) -> { input, cached, output, thinking?, model? }` (optional)
- `iter_text(id) -> yield { role, text, ts }` for search/export
- `supports: { sessions, usage, search, export }`

`SessionSummary`: `id`, `source_id`, `project`/`cwd`, `started_at`, `updated_at`, `title`/`preview`, `size_bytes`, `user_turns?`.

### Security

Reuse companion auth: loopback host, pairing token, local UI origin.  
Paths returned must be absolute and under expected roots (no arbitrary path read API).  
Export writes only under `.repo-local/ai/exports/`.  
Never modify vendor session files.

## UI

### Sidebar

Add **AI** next to existing Repositories / Stats navigation. Route hash: `#/ai` (and `#/ai/sessions`, `#/ai/tokens`, `#/ai/search`, `#/ai/export` as subviews or tabs on one page).

### Views

1. **Detect** — button “Detect”, table grouped by tool: surface, path, status, size, conversations, executions, representations, notes. Cache last run.
2. **Conversations** — filter by tool/surface; list logical conversations and execution counts; open detail later if needed.
3. **Tokens / cost** — aggregate unique provider/model calls by tool, model, project, period, and surface; show token dimensions, partial pricing coverage, reconciliation, and cache quality.
4. **Search** — query box + tool checkboxes; results with snippet + source + session id.
5. **Export** — pick scope (session / source / all wave-1) + format (Markdown, JSONL, CSV summary) → path under `.repo-local/ai/exports/`.

Visual language: match existing Theana sidebar/paper styles in `dist/index.html` (no new design system).

## API (companion)

All JSON, auth as existing `/api/*`.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/ai/detect` | Run detection; persist cache; return catalog results |
| `GET` | `/api/ai/detect` | Last cached detect payload |
| `POST` | `/api/ai/sessions` | `{ sources?: [], project?: "", limit?: n }` |
| `POST` | `/api/ai/usage` | `{ session_ids?: [], sources?: [] }` or aggregate flag |
| `GET`/`POST` | `/api/ai/prices` | Read/update local price table |
| `POST` | `/api/ai/search` | `{ q, sources?, limit? }` |
| `POST` | `/api/ai/export` | `{ scope, format, session_ids?/sources? }` → `{ path, bytes }` |

Errors: 400 validation, 404 unknown session, 503 if search backend unavailable (should not happen with Python fallback).

## Token / cost rules

### Antigravity (confirmed)

From `conversations/<id>.db` → `steps.metadata` on model steps (`step_type=15`), nested usage:

- field 2 → `input_tokens` (uncached)
- field 5 → `cache_read_tokens`
- field 3 → `output_tokens`
- field 9 → thinking; field 10 → visible; **f9+f10 = f3**
- field 1 → model enum (`1318` High, `1319` Medium, `1035` Claude Sonnet 4.6, …)

Prefer `transcript_full.jsonl` for text, not for tokens.

### Other tools

Discover usage fields during implementation; if absent, report `usage_supported: false` rather than inventing counts.

### Pricing

Ship defaults in code; override via `.repo-local/ai/prices.json`:

```json
{
  "currency": "USD",
  "per_million": {
    "gemini-3.8-flash": { "input": 0.75, "cached": 0.075, "output": 3.75 },
    "claude-sonnet-4-6": { "input": 3.0, "cached": 0.30, "output": 15.0 }
  },
  "notes": "Gemini intro rates through 2026-12-31; verify before relying on estimates."
}
```

UI must state: estimates are retail API math on measured tokens; not Antigravity/Claude/Codex subscription invoices; sums are cumulative per call; cache storage fees omitted.

## Search

- Wave 1: scan adapter text streams / known transcript files.
- Prefer `rg --json` when installed; else line-scan in Python with size caps.
- Default limit 50 hits; truncate snippets (~200 chars).
- Do not index secrets specially; treat stores as sensitive logs (same as disk).

Optional later: FTS5 DB under `.repo-local/ai/search.db` if rg scan is too slow.

## Export

| Format | Content |
|--------|---------|
| `markdown` | Title, metadata, user/assistant turns (tools summarized or omitted by flag) |
| `jsonl` | One normalized event per line: `{source, session, ts, role, text}` |
| `csv` | Session-level token/cost summary only |

Filenames: `export-YYYYMMDD-HHMMSS-<scope>.{md,jsonl,csv}`.

## Testing

- Unit tests for catalog paths (tmpdir fixtures), Antigravity usage parser (fixture db or synthetic protobuf blob), detect merge, price math.
- No tests that require the developer’s real `~/.claude` / `~/.codex` contents.
- Companion API tests with temp HOME via env or injected roots.

## Phased delivery

| Phase | Deliverable |
|-------|-------------|
| 0 | Sidebar AI shell + `#/ai` |
| 1 | Detect API + UI + cache (all first-class sources) |
| 2 | Sessions list wave 1 |
| 3 | Tokens/cost wave 1 (Antigravity first, then Claude/Codex/Cursor CLI) |
| 4 | Search wave 1 |
| 5 | Export wave 1 |
| 6 | Wave 2: Claude Desktop + Cursor IDE sessions/search/export/usage as feasible |

Each phase is shippable alone.

## Open questions (non-blocking)

- Whether Claude Desktop `local-agent-mode-sessions` overlaps Claude Code `~/.claude` (investigate in wave 2).
- Whether Codex App Support ever stores rollouts outside `CODEX_HOME` (Detect shows both; sessions adapter uses `~/.codex/sessions`).

## Approval record

- Detect: six first-class families (Antigravity×3, Claude Code, Claude Desktop, Codex, Cursor CLI, Cursor IDE).
- Deep features: wave 1 = CLI-style; wave 2 = Desktop + IDE.
- User confirmed 2026-10-11.
