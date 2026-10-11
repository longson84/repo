# AI Local History Implementation Plan

> Historical implementation plan: pricing and cost-estimation tasks no longer describe the product. The current implementation reports token usage only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an AI sidebar to Theana that detects local AI chat stores, then lists sessions, estimates tokens/cost, searches transcripts, and exports — CLI-style tools first, Desktop/IDE deep parse later.

**Architecture:** Companion gains an `ai/` package (catalog, detect, adapters, prices, search, export). UI adds `#/ai` with Detect → Sessions → Tokens → Search → Export. Data caches under `.repo-local/ai/`. Stdlib only.

**Tech Stack:** Python 3.9+ (existing companion), vanilla `dist/app.js` + `dist/index.html`, unittest, optional system `rg`.

**Spec:** `docs/superpowers/specs/2026-10-11-ai-local-history-design.md`

## Global Constraints

- Loopback + pairing token auth unchanged; no upload.
- No pip dependencies.
- Never modify vendor session files; exports only under `.repo-local/ai/exports/`.
- Tests use tempdirs/fixtures — not the developer’s real home stores.
- Match existing UI patterns in `dist/index.html` / `dist/app.js`.

## Review Focus

- Path safety (no arbitrary filesystem read API).
- Adapter boundaries stay small and testable.
- Price estimates clearly labeled as estimates.

## File structure

| Path | Responsibility |
|------|----------------|
| `companion/ai/__init__.py` | Package export |
| `companion/ai/catalog.py` | Source definitions + env overrides |
| `companion/ai/detect.py` | Size / session estimates / cache IO |
| `companion/ai/prices.py` | Default rates + estimate helpers |
| `companion/ai/search.py` | Query across adapters |
| `companion/ai/export.py` | Markdown / JSONL / CSV writers |
| `companion/ai/adapters/base.py` | Shared types + protocol helpers |
| `companion/ai/adapters/antigravity.py` | App/IDE/CLI |
| `companion/ai/adapters/claude_code.py` | `~/.claude` |
| `companion/ai/adapters/claude_desktop.py` | Detect (+ wave 2 stubs) |
| `companion/ai/adapters/codex.py` | `~/.codex` + app-support detect |
| `companion/ai/adapters/cursor.py` | CLI chats, agent transcripts, IDE detect |
| `companion/server.py` | Wire `/api/ai/*` routes |
| `dist/index.html` | AI nav + view markup/styles |
| `dist/app.js` | AI routes, Detect/Sessions/Tokens/Search/Export UI |
| `tests/test_ai_*.py` | Unit/API tests |
| `tests/fixtures/ai/` | Tiny synthetic session fixtures |
| `.repo-local/ai/` | Runtime cache (gitignored; ensure `.gitignore` covers) |

---

## Phase 0 — AI shell

### Task 0: Sidebar + empty AI view

**Files:** `dist/index.html`, `dist/app.js`, `tests/test_companion.py` (smoke if any UI route tests exist — else skip)

- [ ] Add sidebar nav button **AI** (`data-route` / hash `#/ai`) next to Repositories / Stats.
- [ ] Add `#aiView` container with heading + placeholder copy: “Detect AI discussion stores on this machine.”
- [ ] Wire routing so `#/ai` shows `aiView` and hides overview/stats/detail (mirror `onStats` pattern in `app.js`).
- [ ] Manual check: `python3 run repo` → click AI → empty page renders.
- [ ] Commit: `Add AI sidebar shell with empty view.`

---

## Phase 1 — Detect

### Task 1: Catalog module

**Files:** `companion/ai/catalog.py`, `tests/test_ai_catalog.py`

- [ ] Write failing test: catalog includes `antigravity.app`, `claude.code`, `codex.home`, `cursor.cli`, `claude.desktop`, `cursor.ide`.
- [ ] Write failing test: with `CLAUDE_CONFIG_DIR` / `CODEX_HOME` set, those roots resolve to the override.
- [ ] Implement `SOURCES` list/dicts and `resolve_sources(env=os.environ, home=Path.home())`.
- [ ] Run `python3 -m unittest tests.test_ai_catalog -v` — pass.
- [ ] Commit: `Add AI source catalog with env overrides.`

### Task 2: Detect logic + cache

**Files:** `companion/ai/detect.py`, `tests/test_ai_detect.py`, `tests/fixtures/ai/` (minimal dirs/files)

- [ ] Write failing test: temp home with fake `antigravity` brain + conversation db → `exists=True`, `session_estimate>=1`.
- [ ] Write failing test: missing path → `exists=False`, size 0.
- [ ] Implement `detect_all(sources, cache_path)` computing `size_bytes`, `session_estimate`, `notes` per source (reuse rules from spec).
- [ ] Persist/load `.repo-local/ai/detect.json` (inject path in tests).
- [ ] Run tests — pass.
- [ ] Commit: `Implement AI store detection and cache.`

### Task 3: Detect API

**Files:** `companion/server.py`, `tests/test_companion.py` or `tests/test_ai_api.py`

- [ ] Write failing API test: `POST /api/ai/detect` returns JSON list/groups with auth.
- [ ] Write failing test: `GET /api/ai/detect` returns last cache.
- [ ] Wire routes in `handle_api`; ensure unauthorized rejected like other routes.
- [ ] Run unittest — pass.
- [ ] Commit: `Expose /api/ai/detect endpoints.`

### Task 4: Detect UI

**Files:** `dist/index.html`, `dist/app.js`

- [ ] Detect button + results table grouped by tool (surface, path, status, size, sessions, notes).
- [ ] Call `POST /api/ai/detect`; on load hydrate via `GET` if cache exists.
- [ ] Manual: run against real machine — Antigravity×3, Claude Code, Codex, Cursor CLI/IDE, Claude Desktop show FOUND where present.
- [ ] Commit: `Wire AI Detect page to companion API.`

---

## Phase 2 — Sessions (wave 1)

### Task 5: Adapter base + Antigravity list

**Files:** `companion/ai/adapters/base.py`, `companion/ai/adapters/antigravity.py`, `tests/test_ai_antigravity.py`, fixture db or transcript

- [ ] Define `SessionSummary` (dict or simple namespace).
- [ ] Failing test: fixture with one `transcript_full.jsonl` USER_INPUT → list returns preview + id.
- [ ] Implement list for app/ide/cli roots (`conversations/*.db` ids ∩ brain).
- [ ] Run tests — pass.
- [ ] Commit: `Add Antigravity session listing adapter.`

### Task 6: Claude Code + Codex + Cursor CLI list

**Files:** `companion/ai/adapters/claude_code.py`, `codex.py`, `cursor.py`, tests + tiny fixtures

- [ ] Claude: one jsonl under encoded project dir → one session.
- [ ] Codex: one `rollout-*.jsonl` with `session_meta` → cwd + id.
- [ ] Cursor CLI: one `store.db` (minimal sqlite fixture) or skip deep parse and count db files as sessions with path metadata if schema unknown — prefer real minimal schema probe in fixture.
- [ ] Desktop/IDE adapters: `list_sessions` returns `[]` with `supports.sessions=False` (detect only).
- [ ] Commit: `List sessions for Claude Code, Codex, and Cursor CLI.`

### Task 7: Sessions API + UI

**Files:** `companion/server.py`, `dist/app.js`, `dist/index.html`, tests

- [ ] `POST /api/ai/sessions` with filters.
- [ ] AI page Sessions tab/filter table.
- [ ] Commit: `Add AI sessions API and UI.`

---

## Phase 3 — Tokens / cost (wave 1)

### Task 8: Antigravity usage parser + prices

**Files:** `companion/ai/adapters/antigravity.py`, `companion/ai/prices.py`, `tests/test_ai_usage.py`, `tests/test_ai_prices.py`

- [x] Failing test: synthetic protobuf metadata blob with f2/f5/f3/f9/f10 → parsed counts; f9+f10==f3.
- [x] Failing test: price math for known rates.
- [x] Implement parser + `estimate_cost(usage, prices)`.
- [x] Default `prices.json` writer on first run.
- [ ] Commit: `Parse Antigravity token usage and estimate cost.`

### Task 9: Usage for Claude/Codex/Cursor CLI

**Files:** adapters + tests

- [x] Probe real field names once; implement or mark unsupported.
- [x] Aggregate endpoint `POST /api/ai/usage`.
- [ ] Commit: `Add usage aggregation for wave-1 tools.`

### Task 10: Tokens UI

**Files:** `dist/app.js`, `dist/index.html`

- [x] Tokens view: by tool / model / surface; caveats callout; optional prices note.
- [ ] Commit: `Add AI tokens and cost UI.`

---

## Phase 4 — Search (wave 1)

### Task 11: Search backend

**Files:** `companion/ai/search.py`, tests with fixture transcripts containing a unique needle

- [x] Failing test: search finds needle in Antigravity/Claude fixture.
- [x] Implement rg-or-python scan; limit + snippets.
- [x] `POST /api/ai/search`.
- [ ] Commit: `Add AI transcript search API.`

### Task 12: Search UI

**Files:** `dist/app.js`, `dist/index.html`

- [x] Query box, source filters, results list.
- [ ] Commit: `Add AI search UI.`

---

## Phase 5 — Export (wave 1)

### Task 13: Export writers

**Files:** `companion/ai/export.py`, tests in tmp export dir

- [ ] Markdown + JSONL for one fixture session; CSV summary for usage rows.
- [ ] `POST /api/ai/export` returns path under injected export root.
- [ ] Commit: `Add AI export to markdown, JSONL, and CSV.`

### Task 14: Export UI

**Files:** `dist/app.js`, `dist/index.html`

- [ ] Scope + format controls; show resulting path.
- [ ] Commit: `Add AI export UI.`

---

## Phase 6 — Wave 2 (Desktop + IDE depth)

### Task 15: Claude Desktop + Cursor IDE adapters

**Files:** `claude_desktop.py`, `cursor.py`, tests as feasible

- [ ] Investigate `local-agent-mode-sessions` and `state.vscdb` schemas; implement list/text if stable.
- [ ] Wire into sessions/search/export/usage when `supports` flips true.
- [ ] Document limitations in UI notes if partial.
- [ ] Commit: `Extend AI adapters for Claude Desktop and Cursor IDE.`

---

## Manual verification checklist (end of phase 1+)

- [ ] Detect shows real machine stores without errors.
- [ ] Unauthorized API calls fail.
- [ ] Export directory is gitignored.
- [ ] `python3 -m unittest discover -s tests -v` green.
- [ ] README short “AI” section after phase 1 (optional; update when Detect ships).

## Execution note

Implement **phase 0 → 1 first** and pause for a quick product check before sessions/tokens. Do not start wave 2 until wave 1 search+export work on Antigravity + Claude Code + Codex.
