# Shared Project Identity — Design Spec

**Date:** 2026-10-11
**Status:** Approved
**Product:** Theana / Repo local dashboard

## Intent

Give repository statistics and local AI usage one shared answer to “which project does this activity belong to?” A workspace folder, a Git repository, and a logical product are different identities and must not be collapsed into a single folder basename.

The design must:

- merge casing variants such as `eravnTrans` and `eravntrans`;
- recognize multiple Git worktrees as workspaces of one repository;
- allow several repositories to belong to one logical project;
- preserve workspace-level branch and working-tree state;
- prevent shared Git history from being counted once per worktree;
- regroup historical AI records whose workspaces no longer exist through explicit aliases;
- support period → project → model and period → model → project token reports;
- remain local-only and avoid network access.

## Current Problem

AI adapters derive `project` from the basename of a recorded working directory. Aggregation uses that string as a case-sensitive key. Consequently, the same physical checkout can appear as both `eravnTrans` and `eravntrans`, while historical worktrees such as `projectCompanyQ-plan-6-google` appear as separate projects.

Repository stats are also keyed by selected filesystem path. Portfolio totals add each path’s `commitCount` and daily counts. If two worktrees of one repository are selected, their shared ancestry can be counted twice.

Folder-name suffix inference is not a safe correction. A folder named `product-plan-6` may be a worktree, an independent repository, or an unrelated project. Identity must come from Git metadata or an explicit mapping.

## Identity Model

The system introduces three first-class levels.

### Workspace

A workspace is one concrete working directory used by an AI tool or selected in the repository dashboard. It owns state that can differ across worktrees:

- absolute path;
- display label;
- current branch or detached HEAD;
- dirty, modified, and untracked state;
- ahead/behind status;
- current-tree LOC and file composition;
- source-specific AI surface metadata.

`workspace_id` is the normalized absolute path. Existing paths resolve symlinks and canonicalize each component against directory entries so the filesystem’s real spelling is retained even when a tool recorded different casing. A comparison key uses Unicode case folding on case-insensitive filesystems. Missing historical paths retain their normalized recorded path and can be mapped explicitly.

### Repository

A repository is one logical Git repository shared by one or more worktrees or clones.

Resolution order:

1. Read the absolute Git common directory with local `git rev-parse --path-format=absolute --git-common-dir`. All worktrees sharing this directory receive the same local repository identity.
2. Normalize the primary remote URL when present: remove embedded credentials, normalize SSH/HTTPS forms to `host/owner/name`, remove a trailing `.git`, and case-fold the host. Owner and repository components are also case-folded for known case-insensitive providers such as GitHub; other hosts preserve path casing. The normalized remote is the portable repository key and display source.
3. When no usable remote exists, use the canonical Git common-directory path as the machine-local repository key.

`repository_id` is namespaced as `remote:<normalized-remote>` or `gitdir:<canonical-common-dir>`. Remote credentials and query strings are never persisted or returned.

Multiple clones with the same normalized remote belong to the same logical repository. Each clone remains a separate workspace so local branch and dirty state are preserved.

### Project

A project is the user-facing product or body of work. By default, each repository creates one project. A project may explicitly contain several repositories and historical workspace aliases.

`project_id` is a stable configured slug. `project_name` preserves the chosen display casing. Project membership is never inferred by stripping folder suffixes.

Example:

```text
project: projectCompanyQ
└── repository: github.com/longson84/projectCompanyQ
    ├── workspace: projectCompanyQ
    ├── workspace: projectCompanyQ-plan-6-google
    └── workspace: projectCompanyQ-plan-7-tasks
```

## Configuration

Store explicit logical-project mappings in `.repo-local/project-identities.json` with owner-only permissions and atomic replacement.

```json
{
  "version": 1,
  "projects": [
    {
      "id": "projectcompanyq",
      "name": "projectCompanyQ",
      "repositories": [
        "remote:github.com/longson84/projectcompanyq"
      ],
      "primary_workspaces": {
        "remote:github.com/longson84/projectcompanyq": "/Users/longson/Projects/projectCompanyQ"
      },
      "workspace_aliases": [
        "/Users/longson/Projects/projectCompanyQ-plan-6-google",
        "/Users/longson/Projects/projectCompanyQ-plan-7-tasks"
      ]
    },
    {
      "id": "eravntrans",
      "name": "eravnTrans",
      "workspace_aliases": [
        "/Users/longson/projects/eravntrans"
      ]
    }
  ]
}
```

Repository membership takes precedence over workspace aliases. Exact normalized path aliases take precedence over case-folded basename compatibility aliases. Ambiguous or conflicting rules do not merge activity; they produce an identity warning surfaced in the UI.

The existing tracked repository selection remains in `settings.json`. Identity configuration does not silently add repositories to tracking or perform Git operations that change a checkout.

## Shared Resolver

Create a shared identity module used by repository scanning and AI adapters. Its public operations are:

- resolve a live workspace from an absolute path;
- resolve a historical workspace from a recorded path plus configuration;
- normalize a remote URL without secrets;
- assign repository and project identities;
- return warnings and confidence (`git`, `configured`, `path`, or `unattributed`).

The resolver performs only local filesystem reads and read-only Git commands. Results are cached per scan using path, Git common-directory metadata, and identity-configuration fingerprint.

Normalized records carry:

```json
{
  "workspace_id": "/Users/longson/Projects/projectCompanyQ-plan-6-google",
  "workspace_name": "projectCompanyQ-plan-6-google",
  "repository_id": "remote:github.com/longson84/projectcompanyq",
  "repository_name": "projectCompanyQ",
  "project_id": "projectcompanyq",
  "project_name": "projectCompanyQ",
  "identity_confidence": "configured"
}
```

The legacy `project` field may remain temporarily as a display-name alias in compatibility responses, but grouping uses `project_id`.

## Commit Statistics

### Repository activity

Commit activity is computed once per `repository_id`, not once per workspace path.

For all tracked workspaces belonging to a repository, collect the checked-out commit tips, including detached worktree tips. Run Git history over the union of those tips. Git naturally emits each reachable commit SHA once. All commit counts, time buckets, contributors, and line churn derive from that unique set.

This preserves work done on different worktree branches while preventing shared ancestry from being counted repeatedly.

Repository commit identity is the commit SHA. Project and portfolio commit identity is `(repository_id, commit_sha)` so identical object hashes from two explicitly separate repositories remain separately attributable.

### Workspace state

Branch, detached HEAD, dirty files, ahead/behind, and fetch freshness remain workspace-level. They are displayed below the repository or project and are never averaged or collapsed into a misleading project value.

### Current-tree metrics

LOC, language composition, hotspots, and current-tree size are branch snapshots. For a project total, include one designated primary workspace per repository. Selection order is: explicitly configured primary workspace; the main worktree whose Git directory is the repository common directory; otherwise the lexicographically first existing workspace. The chosen workspace is labeled in the result. Other workspace snapshots remain individually inspectable and are not summed, preventing duplicate source trees. Dirty state does not change primary selection.

For a project containing multiple distinct repositories, project LOC is the sum of one primary snapshot per repository.

### Dashboard totals

Portfolio totals aggregate canonical repositories/projects. They must not sum raw workspace caches. Counts displayed in repository rows remain available, but project and portfolio cards use deduplicated repository activity.

## AI Usage

AI adapters must retain the recorded `cwd` or workspace path in normalized events, then apply the shared resolver. Token call identity and deduplication remain unchanged; only attribution keys change.

Aggregation exposes:

- `by_project`, keyed by `project_id`;
- `by_repository`, keyed by `repository_id`;
- `by_workspace`, keyed by `workspace_id`;
- period buckets with the same nested dimensions;
- identity confidence and warnings.

Project totals include all member repositories and historical workspace aliases. Workspace rows remain available for explaining attribution.

### Token reports

The project view is:

```text
period → project → provider/model
```

The model view mirrors it:

```text
period → provider/model → project
```

Each parent and child row reports input, cached input, output, cache-write, total token volume, unique calls, and percentage share of its parent. Filters and expand/collapse controls operate on canonical display names.

## API and Cache Evolution

The aggregate response version increments because grouping identity and response fields change. Consumers must not interpret the new project groups using the former basename semantics.

AI parser versions increment when workspace identity fields are added. The usage cache remains rebuildable; fingerprint invalidation reparses available source files. The SQLite cache schema increments if new persisted metadata requires it. Existing prompt-free cache privacy rules remain unchanged.

Stats and scan caches increment their versions and are rebuilt from tracked paths. Existing user selections are preserved.

Identity configuration is independent of rebuildable caches and is never deleted during cache migration.

Historical events whose recorded workspace no longer exists resolve through exact configured aliases. Unresolved records remain under `Unattributed`; the system does not guess from suffixes.

## UI

Repository and AI areas use the same project and repository names.

The repository dashboard presents:

- project summary rows;
- nested repositories when a project contains more than one;
- nested workspace status rows;
- deduplicated commit totals at repository and project levels;
- workspace-specific branch, dirty, ahead/behind, and freshness values.

The AI Tokens view presents canonical project and model drill-downs. An attribution-quality callout reports unresolved and configured-alias event counts and links them to workspace rows without exposing transcript content.

## Error Handling and Safety

- Missing Git, invalid repositories, and deleted workspaces fall back to configured/path identity with warnings rather than dropping activity.
- Conflicting project memberships remain separate and visible until corrected.
- Remote normalization removes user info, passwords, tokens, query parameters, and fragments before persistence.
- All Git commands are local and read-only. No fetch, checkout, pull, commit, branch mutation, or worktree creation occurs.
- Identity files and caches use owner-only permissions and atomic writes.
- No prompt, response, snippet, or tool payload is added to caches or reports.

## Testing

Automated coverage must include:

1. casing variants resolve to one live workspace/project while preserving display casing;
2. two worktrees with one Git common directory share a repository identity;
3. two independent repositories with the same basename remain separate;
4. SSH and HTTPS forms of one credential-free remote normalize identically;
5. credentials in remote URLs are stripped;
6. deleted historical workspaces resolve only through explicit aliases;
7. conflicting aliases remain unmerged and emit warnings;
8. shared worktree ancestry contributes each commit SHA once;
9. distinct worktree branch commits are both included;
10. different repositories use `(repository_id, commit_sha)` at project level;
11. LOC uses one primary workspace per repository;
12. AI project/repository/workspace totals reconcile for every token dimension and call count;
13. period → project → model and period → model → project totals reconcile at day/week/month/year grains;
14. cache migration preserves selections and identity configuration while rebuilding derived data;
15. API and UI contain no pricing or cost fields.

Real-UI acceptance must verify project/model expansion, filters, period navigation, identity warnings, workspace status, and responsive table behavior.

## Rollout

1. Add the shared resolver and identity configuration with synthetic Git/worktree tests.
2. Migrate repository discovery, caches, and commit aggregation to workspace/repository/project records.
3. Add unique commit union and primary-workspace current-tree rules.
4. Add identity fields to AI adapters, increment parser/cache versions, and rebuild usage attribution.
5. Migrate API responses and both token drill-down directions.
6. Update repository and AI UI hierarchy.
7. Seed explicit historical mappings for `eravnTrans` and `projectCompanyQ` examples after showing the proposed mapping to the user.
8. Run unit, API, cache, Git-worktree, live local aggregation, and native-browser acceptance gates.

## Decisions

- Git metadata and explicit configuration are authoritative; folder suffix heuristics are forbidden.
- Workspaces keep operational state; repositories own deduplicated commit history; projects own cross-repository reporting.
- AI token events are never deduplicated merely because workspaces merge; existing provider-call identity remains authoritative.
- Commit stats use the union of tracked worktree tips, deduplicated by SHA within a repository.
- Pricing remains absent from runtime, API, UI, and reports.
- Historical source data is reparsed when available; unresolved attribution stays visible rather than being invented.
