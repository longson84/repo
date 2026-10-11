# Project Grouping UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe bulk UI for grouping multiple canonical Git repositories into persisted logical projects without changing repository tracking.

**Architecture:** Extend the existing identity API with optimistic concurrency and server-side name validation, put pure configuration transformations in a small browser module, and layer the grouping workflow into the existing Manage tracked repos drawer. Successful writes continue through the atomic local identity writer, invalidate stale derived data, and trigger the existing local scan/stat rebuild.

**Tech Stack:** Python 3 standard library, plain browser JavaScript, HTML/CSS, `unittest`, Node `vm` executable UI tests, Git CLI read-only inspection.

**Spec:** `docs/superpowers/specs/2026-10-11-project-grouping-ui-design.md`

## Global Constraints

- Keep identity config version `1`; do not introduce another persistent store.
- Keep grouping selection independent from tracked workspace selection.
- A canonical `repository_id` belongs to at most one configured project.
- Preserve unedited repositories, aliases, primary-workspace mappings, and hidden/untracked configuration entries.
- Persist only through the authenticated loopback API and atomic mode-`0600` writer.
- Never expose remote credentials and never add fetch, checkout, pull, commit, branch mutation, or worktree creation.
- A project save must not change `.repo-local/settings.json` or `State.paths`.
- No new runtime or package dependency.
- Use TDD: each behavior test must fail for the expected missing behavior before product code is added.
- Work directly on `main` as previously requested; do not push.

## Review Focus

1. Two selected workspace paths resolving to one repository must create one membership and show synchronized assignment, not duplicates.
2. A project containing configured repositories absent from the current discovery/tracking list must preserve them through every ordinary edit.
3. Two open editors must not overwrite one another; the stale fingerprint must receive `409 identity_config_conflict` and retain the newer file.
4. Rename, ungroup, and delete must preserve advanced aliases/primary mappings or refuse the destructive action explicitly.
5. A successful identity save followed by a failed stats refresh must keep the saved grouping, mark reports stale, and offer retry rather than reverting or presenting old totals.

---

### Task 1: Make Identity Writes Conflict-Safe

**Files:**
- Modify: `companion/project_identity.py`
- Modify: `companion/server.py`
- Test: `tests/test_project_identity.py`
- Test: `tests/test_companion.py`
- Test: `tests/test_ai_api.py`

**Interfaces:**
- Produces: `IdentityConfigConflict(ValueError)`.
- Produces: identity API responses `{version, projects, warnings, fingerprint}`.
- Produces: optional POST field `expected_fingerprint: string`; mismatch returns HTTP `409` with code `identity_config_conflict`.
- Preserves: POSTs without `expected_fingerprint` for backward-compatible internal callers.

- [ ] **Step 1: Write failing config-validation tests**

Add `ConfigTests.test_project_names_are_unique_case_insensitively` in `tests/test_project_identity.py`. Assert `Alpha` and ` alpha ` in separate projects raise `ValueError` containing `name`, while the normalized saved names remain trimmed.

- [ ] **Step 2: Run the focused validation test and confirm RED**

Run: `python3 -m unittest tests.test_project_identity.ConfigTests.test_project_names_are_unique_case_insensitively -v`
Expected: FAIL because duplicate display names are currently accepted.

- [ ] **Step 3: Add strict project-name validation**

In `_normalize_config(payload, *, strict)` track `name.casefold()` ownership after trimming. Reject duplicate names only when `strict=True`; continue allowing a resolver constructed from externally conflicting data to surface warnings rather than crash.

- [ ] **Step 4: Run project identity tests**

Run: `python3 -m unittest tests.test_project_identity -v`
Expected: PASS.

- [ ] **Step 5: Write failing state, preservation, and HTTP conflict tests**

Add:

- `CompanionTests.test_identity_payload_refreshes_external_changes_and_invalidates_derived_caches`;
- `CompanionTests.test_stale_identity_fingerprint_does_not_overwrite_newer_file`;
- `AiApiTests.test_project_identity_api_returns_fingerprint_and_rejects_stale_write`;
- `AiApiTests.test_project_grouping_save_preserves_tracking_and_advanced_fields`.

Use two fingerprints over version-1 documents. Assert the stale save leaves the newer bytes and tracked settings unchanged. At HTTP level assert status `409`, code `identity_config_conflict`, and that a fresh GET returns the newer project. For the successful-save case, assert `settings.json` bytes and tracked paths are unchanged, hidden repositories plus alias/primary mappings survive, the next status response uses the new identity, and the identity file remains mode `0600`.

- [ ] **Step 6: Run the focused conflict tests and confirm RED**

Run: `python3 -m unittest tests.test_companion.CompanionTests.test_stale_identity_fingerprint_does_not_overwrite_newer_file tests.test_ai_api.AiApiTests.test_project_identity_api_returns_fingerprint_and_rejects_stale_write tests.test_ai_api.AiApiTests.test_project_grouping_save_preserves_tracking_and_advanced_fields -v`
Expected: FAIL because responses lack fingerprints and writes are unconditional; the preservation test must remain green once the guarded write exists.

- [ ] **Step 7: Implement conflict-safe identity state**

In `companion/server.py`:

- add `IdentityConfigConflict(ValueError)`;
- make `identity_payload()` reload the disk document, compare `ProjectIdentityResolver(...).fingerprint()`, and invalidate scan/stat caches only when the on-disk identity changed;
- make `save_identity_config(payload, expected_fingerprint=None)` reload the disk document while the handler lock is held, compare fingerprints when supplied, then save and install the normalized config;
- return the active fingerprint from `identity_payload()`;
- map `IdentityConfigConflict` to `409 {"error": ..., "code": "identity_config_conflict"}` before the general `ValueError` handler;
- pass `data.get("expected_fingerprint")` from the POST route while writing only `version` and `projects` to disk.

- [ ] **Step 8: Run API/state tests**

Run: `python3 -m unittest tests.test_project_identity tests.test_companion tests.test_ai_api -v`
Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```bash
git add companion/project_identity.py companion/server.py tests/test_project_identity.py tests/test_companion.py tests/test_ai_api.py
git commit -m "feat: guard project identity updates"
```

### Task 2: Add Pure Project Configuration Operations

**Files:**
- Create: `dist/project_groups.js`
- Create: `tests/test_project_groups_ui.py`
- Modify: `dist/index.html`
- Modify: `tests/test_dashboard_ui.py`

**Interfaces:**
- Produces: `slugProjectId(name, projects) -> string`.
- Produces: `projectNameError(projects, name, exceptId = '') -> string | null`.
- Produces: `assignRepositories(config, repositoryIds, target) -> {config, projectId}` where `target` is `{projectId}` or `{name}`.
- Produces: `makeRepositoriesStandalone(config, repositoryIds) -> config`.
- Produces: `renameConfiguredProject(config, projectId, name) -> config`.
- Produces: `deleteConfiguredProject(config, projectId) -> config`, throwing when advanced mappings exist.
- All operations return new normalized-shaped objects and do not mutate input.

- [ ] **Step 1: Add the executable module harness and failing slug/name tests**

Create `tests/test_project_groups_ui.py` with the same Node `vm` pattern as `tests/test_dashboard_ui.py`, loading `dist/project_groups.js`. Add literal assertions for:

- `"  General Tools  " -> "general-tools"`;
- Vietnamese/Unicode normalization;
- collision sequence `general`, `general-2`, `general-3`;
- empty name error;
- `Alpha` conflicting with ` alpha ` except when renaming the same project.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python3 -m unittest tests.test_project_groups_ui -v`
Expected: ERROR because `dist/project_groups.js` and its functions do not exist.

- [ ] **Step 3: Implement slug and validation functions**

Create `dist/project_groups.js` as a plain strict-mode browser script with global functions. Use `String.prototype.normalize('NFKD')`, remove combining marks, apply the spec's `[a-z0-9._-]` rule, and allocate numeric suffixes deterministically.

- [ ] **Step 4: Run focused tests until slug/name cases pass**

Run: `python3 -m unittest tests.test_project_groups_ui -v`
Expected: slug/name tests PASS.

- [ ] **Step 5: Add failing immutable mutation tests**

Add tests that assert:

- two repository IDs move from different projects into one new project once each;
- a moved repository carries only its matching primary mapping;
- hidden repository memberships and unrelated aliases remain byte-equivalent as parsed values;
- duplicate workspace-derived IDs are deduplicated;
- standalone removes membership and primary mapping, pruning only truly empty projects;
- rename preserves ID, repositories, aliases, and primaries;
- delete refuses aliases or primaries and safely removes an ordinary project;
- original input objects are unchanged.

- [ ] **Step 6: Run mutation tests and confirm RED**

Run: `python3 -m unittest tests.test_project_groups_ui -v`
Expected: ERROR for missing mutation functions.

- [ ] **Step 7: Implement immutable project operations**

Use deep copies of the small JSON document. Centralize repository removal/moved-primary collection in a private helper so assign and standalone use identical preservation rules. Keep all configuration fields not owned by the operation.

- [ ] **Step 8: Load the module before `app.js` and update the dashboard harness**

Add `<script src="project_groups.js"></script>` before `app.js` in `dist/index.html`. Update `tests/test_dashboard_ui.py` to load both scripts in browser order so later UI tasks exercise the real dependency.

- [ ] **Step 9: Run JavaScript and UI module checks**

Run: `node --check dist/project_groups.js && node --check dist/app.js && python3 -m unittest tests.test_project_groups_ui tests.test_dashboard_ui -v`
Expected: PASS.

- [ ] **Step 10: Commit Task 2**

```bash
git add dist/project_groups.js dist/index.html tests/test_project_groups_ui.py tests/test_dashboard_ui.py
git commit -m "feat: add project grouping operations"
```

### Task 3: Separate Group Selection from Repository Tracking

**Files:**
- Modify: `dist/app.js`
- Modify: `dist/index.html`
- Modify: `tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: identity API fingerprint from Task 1.
- Consumes: pure operations loaded by Task 2.
- Produces: browser state `workspaces`, refreshed by `applyStatus(result)`.
- Produces: `projectManagerRows(choices, selectedRepositoryIds, identityConfig) -> string`.
- Produces: `projectManagerModel(paths, workspaces, discovered) -> Map<workspace_id, row>` with canonical repository/project fields.

- [ ] **Step 1: Write failing manager-model tests**

In `tests/test_dashboard_ui.py`, add tests asserting:

- tracked paths merge with canonical `status.workspaces` evidence;
- a discovered untracked workspace remains available with `tracked: false`;
- two workspaces sharing a `repository_id` both render selected when that repository is selected;
- a row without `repository_id` disables grouping but retains its tracking control.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python3 -m unittest tests.test_dashboard_ui.DashboardUiTests.test_project_manager_model_keeps_tracking_separate tests.test_dashboard_ui.DashboardUiTests.test_project_manager_rows_select_repository_identity_once -v`
Expected: ERROR for missing manager helpers.

- [ ] **Step 3: Retain canonical workspaces in application state**

Add `workspaces = []` beside `paths`. Update `applyStatus`, `clearPairing`, connection setup, selection responses, and discovery merge paths so canonical identity evidence is retained without changing legacy `paths` behavior.

- [ ] **Step 4: Implement pure manager model and row rendering**

Render distinct controls with these stable attributes:

- `data-group-repository="<repository_id>"` for grouping selection;
- `data-track-workspace="<workspace_id>"` for tracking;
- `data-current-project="<project_id>"` on row metadata.

Labels must say “Select <repository> for grouping” and “Track <workspace>”. Do not reuse one checkbox for both actions.

- [ ] **Step 5: Refactor `manage()` to load identity data before enabling grouping**

Open the drawer immediately in loading state, request `project-identities`, build choices from `paths + workspaces`, then enable grouping. Preserve the existing debounced `/api/selection` write for tracking only. Discovery adds canonical rows returned by `/api/discover` without selecting or tracking them automatically.

- [ ] **Step 6: Bind synchronized repository selection**

Maintain `selectedRepositoryIds: Set<string>` within the drawer. Changing one grouping checkbox updates the set and rerenders all rows sharing that repository ID. Tracking switches only modify the workspace choice and call the existing selection API.

- [ ] **Step 7: Run focused and full dashboard tests**

Run: `node --check dist/app.js && python3 -m unittest tests.test_dashboard_ui -v`
Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add dist/app.js dist/index.html tests/test_dashboard_ui.py
git commit -m "feat: separate tracking and grouping selection"
```

### Task 4: Build Bulk Assignment and Project Lifecycle UI

**Files:**
- Modify: `dist/app.js`
- Modify: `dist/index.html`
- Test: `tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: Task 2 mutation functions.
- Consumes: `projectManagerRows(...)` and drawer-local selection from Task 3.
- Produces: `projectBulkEditor(config, selectedRepositoryIds, mode) -> string`.
- Produces: `configuredProjectCards(config, visibleRepositoryIds) -> string`.
- Produces: drawer actions for assign/create/standalone, rename, ungroup, and delete.

- [ ] **Step 1: Write failing renderer tests**

Add executable tests that assert:

- the bulk toolbar reports the count of unique repository IDs;
- the editor lists existing projects, New project, and Standalone;
- project cards report repository and visible-workspace counts;
- hidden configured repositories produce a “not currently visible” count;
- advanced mappings disable Delete with explanatory text;
- narrow markup groups row controls in a wrapping action container.

- [ ] **Step 2: Run renderer tests and confirm RED**

Run: `python3 -m unittest tests.test_dashboard_ui.DashboardUiTests.test_project_bulk_editor_options tests.test_dashboard_ui.DashboardUiTests.test_project_cards_protect_advanced_mappings -v`
Expected: ERROR for missing renderers.

- [ ] **Step 3: Implement bulk toolbar and inline editor**

Add **Group selected** and **Clear selection** to the manager. The inline editor has one mode select, an existing-project select, a new-name input shown only for New project, **Apply**, and **Cancel**. Applying with no selected repositories or invalid name displays a drawer status error without issuing a request.

- [ ] **Step 4: Implement project cards and lifecycle editors**

Render Rename, Ungroup repositories, and Delete controls per configured project. Use inline confirmation panels rather than nested dialogs. Delete remains disabled when aliases or primaries exist. Every operation transforms the drawer's working copy with Task 2 functions before persistence.

- [ ] **Step 5: Add interaction tests with executable DOM doubles**

Extend the Node harness only enough to execute the real bindings. Assert:

- grouping selection never triggers `/api/selection`;
- tracking changes do trigger only `/api/selection`;
- Apply transforms the working copy with deduplicated repository IDs and hands the complete draft to a stubbed persistence boundary;
- Cancel and successful actions restore focus to their trigger;
- server validation text appears in `settingsError` without replacing the working config.

- [ ] **Step 6: Run UI tests**

Run: `node --check dist/app.js && python3 -m unittest tests.test_dashboard_ui -v`
Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add dist/app.js dist/index.html tests/test_dashboard_ui.py
git commit -m "feat: manage logical projects in the dashboard"
```

### Task 5: Persist Grouping and Refresh Reports Safely

**Files:**
- Modify: `dist/app.js`
- Modify: `tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: conflict-safe identity API from Task 1.
- Produces: `saveProjectIdentityDraft(draft, fingerprint) -> Promise<response>`.
- Produces: `aiUsageInvalidatedByIdentity: boolean` and a Tokens-panel stale notice.
- Produces: retryable post-save repository refresh behavior.

- [ ] **Step 1: Write failing browser save/refresh tests**

Add tests for:

- request body includes `expected_fingerprint` and complete `version/projects`;
- `api()` exposes HTTP status and response `code` on errors;
- `409 identity_config_conflict` reloads the current document and keeps the editor open;
- successful save clears `aiUsage`, sets the stale notice, closes the drawer, and initiates refresh;
- refresh failure leaves the new identity config installed, reports “Project saved; scan failed,” and offers a Scan retry without restoring old totals.

- [ ] **Step 2: Run browser tests and confirm RED**

Run: `python3 -m unittest tests.test_dashboard_ui -v`
Expected: FAIL because save orchestration and stale-report state do not exist.

- [ ] **Step 3: Preserve structured API errors**

Update `api(path, body)` so thrown errors carry `status` and response `code` while retaining the current human-readable message and timeout behavior.

- [ ] **Step 4: Implement save orchestration**

`saveProjectIdentityDraft` posts the complete draft plus fingerprint. On success install returned config/fingerprint, clear loaded AI aggregation, set the identity-stale notice, close the drawer, and run the existing scan/stat flow. On conflict reload identities and rerender the editor with a repeat-action message. On other failures keep the working draft and editor open.

- [ ] **Step 5: Render AI attribution staleness explicitly**

When identity changes, the Tokens tab must say project grouping changed and require **Aggregate**. A successful aggregate clears `aiUsageInvalidatedByIdentity`. No previous project/model totals remain in the table after the save.

- [ ] **Step 6: Run API/UI/full tests**

Run: `python3 -m unittest tests.test_ai_api tests.test_dashboard_ui -v && python3 -m unittest discover -s tests -v`
Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add dist/app.js tests/test_dashboard_ui.py
git commit -m "feat: persist project grouping from the UI"
```

### Task 6: Polish, Document, and Run Native Acceptance

**Files:**
- Modify: `dist/index.html`
- Modify: `dist/app.js`
- Modify: `README.md`
- Modify: `tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: completed grouping workflow.
- Produces: responsive, keyboard-usable manager and documented local behavior.

- [ ] **Step 1: Write failing layout/focus behavior tests**

Add executable assertions that opening bulk/rename editors focuses the first control, cancel restores the trigger, rows expose distinct accessible labels, and rendered containers use stable classes for wrapping controls/project cards. The production changes that must make these fail are removal of focus restoration, label association, or responsive container structure.

- [ ] **Step 2: Run focused UI tests and confirm RED**

Run: `python3 -m unittest tests.test_dashboard_ui -v`
Expected: FAIL on missing accessibility/responsive behavior.

- [ ] **Step 3: Add scoped responsive styles**

In `dist/index.html`, style `.project-bulk-toolbar`, `.project-manager-row`, `.project-row-actions`, `.project-editor`, and `.configured-project-card`. At `max-width: 760px`, stack metadata and controls without horizontal page overflow. Preserve existing dashboard typography and colors.

- [ ] **Step 4: Complete focus and status behavior**

Use the existing drawer focus trap. After rerenders, restore focus by stable project/repository data attributes. Use `settingsError` as the live status region for loading, validation, save, conflict, and refresh messages.

- [ ] **Step 5: Document the UI workflow**

Update `README.md` with: selection versus tracking, create/existing/standalone operations, safe deletion limits, local persistence, automatic report refresh, and the need to re-Aggregate AI usage after grouping changes.

- [ ] **Step 6: Run automated final gates**

Run:

```bash
git diff --check
node --check dist/project_groups.js
node --check dist/app.js
python3 -m compileall -q companion
python3 -m unittest discover -s tests -v
git status --short
```

Expected: all commands exit `0`; only intended files are modified before the task commit.

- [ ] **Step 7: Run authenticated local API acceptance**

Start with `python3 run repo`. Through the loopback API, record the tracked path list and original identity document, then create a temporary `General` grouping from at least two real small repository IDs. Verify settings/paths are unchanged and project/repository/workspace stats reconcile. Use a `try/finally` acceptance helper so the original identity document is restored with the latest fresh fingerprint even when an assertion fails, and verify the restored document semantically matches the original. Never expose the pairing token in committed output.

- [ ] **Step 8: Run native browser acceptance**

Verify in the local dashboard:

- grouping selection and Tracked switches are visually distinct;
- bulk create/existing/standalone actions work;
- rename/ungroup/delete protections work;
- shared-repository workspace rows stay synchronized;
- conflict, validation, and refresh messages are understandable;
- stats show the new project after refresh and AI requests Aggregate again;
- keyboard focus returns after every inline editor action;
- the drawer remains usable at a narrow viewport.

If computer-use permission is unavailable, record the exact blocked gate and do not claim native-browser acceptance.

- [ ] **Step 9: Commit Task 6**

```bash
git add dist/index.html dist/app.js README.md tests/test_dashboard_ui.py
git commit -m "docs: explain dashboard project grouping"
```

- [ ] **Step 10: Review the complete implementation**

Review `9a92655..HEAD` against the approved spec and all five Review Focus items. Fix every Critical/Important finding with a failing regression test and a separate commit. Re-run the final gates and report local-only evidence; do not claim CI, remote push, or deployment.
