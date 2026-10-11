# Project Grouping UI — Design Specification

**Date:** 2026-10-11  
**Status:** Approved
**Product:** Theana / Repo local dashboard

## Intent

Let a user combine several Git repositories into one logical project from the local dashboard, without manually editing `.repo-local/project-identities.json`.

The first target is the common case where many small repositories belong to one broader project such as `General`. The workflow must remain local-only, preserve the existing repository tracking behavior, and use the canonical workspace → repository → project identity model already used by Git statistics and AI usage.

## Success Criteria

- A user can select several repositories and assign them to a new or existing project.
- Repository grouping and repository tracking are separate actions with separate controls.
- A repository belongs to at most one configured project.
- Multiple workspaces or clones of the same canonical repository move together.
- Projects can be created, renamed, ungrouped, and deleted safely.
- Existing historical aliases and primary-workspace settings are never lost through an unrelated edit.
- Configuration persists through the existing atomic, owner-only identity writer.
- Repository reports rebuild after a change; previously loaded AI totals cannot remain visibly attributed using stale grouping.
- Invalid or failed saves do not partially change the UI or persisted configuration.

## Chosen Interaction

Extend the existing **Manage tracked repos** drawer with bulk project assignment. This keeps repository discovery, tracking, and logical grouping in one workflow without adding another navigation screen.

Each workspace row has two independent controls:

1. a row-selection checkbox used only for project bulk actions;
2. a **Tracked** switch used only to include or exclude that workspace from reports.

The row also shows its canonical repository and current project as compact labels. Selecting a workspace selects its canonical repository for grouping. If another visible workspace resolves to the same `repository_id`, both rows show the same selected state and project assignment because grouping operates at repository level.

The bulk toolbar contains:

- selection count;
- **Group selected**;
- **Clear selection**.

Choosing **Group selected** opens an inline editor inside the drawer. The user can:

- select an existing configured project;
- enter a name for a new project;
- choose **Standalone** to remove the selected repositories from custom projects.

The existing tracking switches continue to save promptly. Project changes save only when the inline editor's explicit **Apply** button is pressed.

## Project Management

A project section below the repository list shows configured project cards with:

- display name;
- repository count;
- visible workspace count;
- an indication when configured repositories are not currently visible or tracked;
- **Rename**;
- **Ungroup repositories**;
- **Delete project**.

Rename changes only `name`; it preserves the stable project `id` and every repository, alias, and primary-workspace mapping.

Ungroup removes selected repository memberships from that project. Those live repositories return to the default one-project-per-repository behavior. If any selected repository has a primary-workspace mapping, the UI refuses the operation instead of silently discarding that mapping. If the project becomes empty and has no historical aliases or primary-workspace mappings, its empty configuration entry is removed. Otherwise the entry is retained.

Delete removes the project entry only when it has no `workspace_aliases` and no `primary_workspaces`. A project containing either form of advanced identity evidence cannot be deleted in the UI. Historical aliases can remain while live repositories are ungrouped, but a primary-workspace mapping must be removed explicitly before its repository can be ungrouped. Deleting a permitted project never deletes repositories, workspaces, Git data, AI events, or tracked selections.

## Project Names and IDs

Display names must be non-empty after trimming and unique case-insensitively within the configuration.

Creating a project generates its stable ID once:

1. Unicode-normalize the display name and remove combining marks.
2. Convert to lowercase.
3. Replace runs outside `[a-z0-9._-]` with `-`.
4. Trim punctuation from both ends.
5. Fall back to `project` when no valid characters remain.
6. Append `-2`, `-3`, and so on until the ID is unique.

Renaming never regenerates the ID.

## Data Sources

The UI combines three existing local API responses:

- `GET /api/status` supplies tracked paths and canonical workspace/repository/project identities;
- `POST /api/discover` supplies canonical identity fields for newly discovered workspaces;
- `GET /api/project-identities` supplies durable configured projects, repository memberships, aliases, primary workspaces, and warnings.

No new persistent store is introduced. `POST /api/project-identities` remains the only write path and `.repo-local/project-identities.json` remains the source of truth.

The browser keeps a working copy of the identity document only while the drawer is open. Every save is built from the latest successfully loaded document, so fields the UI does not edit are copied through unchanged.

Identity responses include a deterministic configuration fingerprint. A write includes the fingerprint it was based on, and the server compares it while holding the existing state lock before replacing the file. If another tab or local editor changed the configuration, the server returns a conflict instead of overwriting newer mappings. The drawer reloads the current document and asks the user to repeat the intended action.

## Mutation Rules

### Assign to an existing project

For every selected canonical `repository_id`:

1. remove it from every project's `repositories` list;
2. remove any primary-workspace entry for that repository only from the previous project;
3. add it once to the target project's `repositories` list;
4. preserve all unrelated repositories, aliases, and primary mappings.

A primary-workspace mapping follows the repository when possible: if the previous project has a primary path for the selected repository, move that mapping to the target project rather than dropping it.

### Create and assign

Create the normalized project entry, then apply the same reassignment operation. New projects begin with empty `workspace_aliases` and only the primary mappings moved with their selected repositories.

### Make standalone

Refuse the operation when a selected repository has a primary mapping. Otherwise remove the selected repository IDs from configured projects. Do not create explicit standalone configuration; the resolver's default repository identity becomes authoritative.

### Rename

Update only the matching project's display name after name validation.

### Delete

Reject deletion when advanced mappings are present. Otherwise remove the project entry. Its repositories immediately fall back to standalone identity.

## Save and Refresh Flow

1. Disable project mutation controls while saving.
2. POST the complete versioned identity document.
3. Replace the drawer's working copy only with the normalized successful response.
4. Clear loaded AI usage from browser state and show that Aggregate must be run again.
5. Close the drawer and run the existing repository scan/stat refresh with progress feedback.
6. Render the new project hierarchy when the refresh completes.

The existing server behavior invalidates derived scan/stat caches when identity configuration changes. The tracked path selection is not modified by a project save.

## Error Handling

- The drawer disables grouping until status and identity configuration load successfully.
- Missing `repository_id` evidence disables project selection for that row and shows a local identity warning.
- Empty or case-insensitive duplicate project names are rejected in both the browser and server validation.
- A stale configuration fingerprint produces a conflict and reload prompt rather than a lost update.
- Server validation errors appear beside the inline editor and leave its working copy intact.
- Network or companion failures leave the last successfully persisted assignments visible and allow retry.
- Conflicting or invalid configuration warnings remain visible; the UI never guesses membership.
- A long repository refresh reports progress through the existing busy state and does not make a second concurrent request.

## Accessibility and Responsive Behavior

- Selection and tracking controls have distinct labels containing the repository/workspace name.
- The grouping panel uses normal buttons, inputs, and selects within the existing focus-trapped drawer.
- Opening an editor moves focus to its first field; success or cancellation restores focus to the triggering control when it still exists.
- Status and validation messages use the existing live status region.
- At narrow widths, row metadata stacks while controls remain reachable without horizontal page scrolling.
- Project cards and bulk controls wrap rather than truncate project names or repository counts.

## Security and Privacy

- All requests continue through the loopback-only authenticated API.
- The UI sends canonical credential-free `repository_id` values, never raw remote URLs containing secrets.
- The identity file remains gitignored, atomically replaced, and mode `0600`.
- No Git mutation or network fetch is introduced.
- No prompt, response, transcript, or source-file content is read by this workflow.

## Testing

Automated coverage must include:

1. creating a unique stable slug from a project name;
2. rejecting empty and case-insensitive duplicate names;
3. assigning multiple repositories to a new project;
4. reassigning repositories atomically from another project;
5. moving a repository's primary-workspace mapping with it;
6. returning repositories to standalone behavior;
7. renaming without changing the project ID or advanced fields;
8. safe deletion and refusal when aliases or primary mappings exist;
9. preserving hidden/untracked configured repositories and all unrelated fields;
10. deduplicating multiple selected workspaces with one `repository_id`;
11. keeping tracking toggles independent from grouping selection;
12. persistence and cache invalidation through the authenticated API;
13. stale-fingerprint conflict handling across two simulated editors;
14. executable UI rendering and interaction tests for the bulk editor, project cards, validation, focus restoration, and narrow layout structure;
15. repository stats and AI aggregation using the new assignment after refresh.

## Acceptance

Using only the dashboard, a user can select several small repositories, create a project named `General`, and see those repositories grouped under one `General` project in repository statistics. After running AI Aggregate, their usage also appears under `General`. Tracking choices remain unchanged, configuration survives restart, advanced mappings remain intact, and ungrouping restores the repositories as independent projects.
