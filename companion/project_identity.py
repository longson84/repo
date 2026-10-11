"""Canonical local workspace, repository, and logical-project identity."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import unquote, urlparse


EMPTY_CONFIG = {"version": 1, "projects": []}
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SCP_REMOTE_RE = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):(.+)$")
CASE_INSENSITIVE_REMOTE_HOSTS = {"github.com"}


def _canonical_components(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    anchor = Path(absolute.anchor)
    current = anchor
    for part in absolute.parts[1:]:
        chosen = part
        if current.is_dir():
            try:
                matches = [
                    child.name
                    for child in current.iterdir()
                    if child.name.casefold() == part.casefold()
                ]
            except OSError:
                matches = []
            if part in matches:
                chosen = part
            elif len(matches) == 1:
                chosen = matches[0]
        current = current / chosen
    return current


def canonicalize_workspace_path(value: str, *, must_exist: bool = False) -> str:
    """Return an absolute, symlink-resolved path with observed component casing."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("workspace path must be a non-empty string")
    path = _canonical_components(Path(value.strip()))
    try:
        resolved = path.resolve(strict=must_exist)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"workspace path cannot be resolved: {value}") from error
    if must_exist and not resolved.exists():
        raise ValueError(f"workspace path does not exist: {value}")
    return str(resolved)


def normalize_remote_url(value: str) -> Optional[str]:
    """Normalize a supported Git remote without retaining credentials or secrets."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or any(char.isspace() for char in raw):
        return None

    match = SCP_REMOTE_RE.match(raw) if "://" not in raw else None
    if match:
        host = match.group(1).lower().rstrip(".")
        remote_path = match.group(2)
    else:
        parsed = urlparse(raw)
        if parsed.scheme.lower() not in {"http", "https", "ssh", "git"}:
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
        remote_path = parsed.path
    if not host:
        return None
    parts = [unquote(part) for part in remote_path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    if parts[-1].lower().endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if not parts[-1] or any(part in {".", ".."} for part in parts):
        return None
    if host in CASE_INSENSITIVE_REMOTE_HOSTS:
        parts = [part.casefold() for part in parts]
    return "/".join([host, *parts])


def _normalize_repository_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("repository identity must be a non-empty string")
    repository_id = value.strip()
    if repository_id.startswith("remote:"):
        portable = repository_id[len("remote:") :]
        if "@" in portable or "?" in portable or "#" in portable:
            raise ValueError("repository identity must not contain credentials or secrets")
        normalized = normalize_remote_url("https://" + portable)
        if not normalized:
            raise ValueError("invalid remote repository identity")
        return "remote:" + normalized
    if repository_id.startswith("gitdir:"):
        return "gitdir:" + canonicalize_workspace_path(
            repository_id[len("gitdir:") :]
        )
    raise ValueError("repository identity must start with remote: or gitdir:")


def _normalize_config(payload: Mapping[str, Any], *, strict: bool) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("identity configuration must be an object")
    if payload.get("version") != 1:
        raise ValueError("identity configuration version must be 1")
    projects = payload.get("projects")
    if not isinstance(projects, list):
        raise ValueError("identity configuration projects must be a list")

    normalized_projects = []
    project_ids: set[str] = set()
    project_name_owners: dict[str, str] = {}
    repository_owners: dict[str, str] = {}
    alias_owners: dict[str, str] = {}
    for raw_project in projects:
        if not isinstance(raw_project, Mapping):
            raise ValueError("each project identity must be an object")
        project_id = raw_project.get("id")
        name = raw_project.get("name")
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("project id must be a lowercase slug")
        if project_id in project_ids:
            raise ValueError(f"duplicate project id: {project_id}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("project name must be a non-empty string")
        normalized_name = name.strip()
        name_owner = project_name_owners.get(normalized_name.casefold())
        if strict and name_owner and name_owner != project_id:
            raise ValueError(f"duplicate project name: {normalized_name}")
        project_name_owners.setdefault(normalized_name.casefold(), project_id)
        project_ids.add(project_id)

        raw_repositories = raw_project.get("repositories", [])
        raw_aliases = raw_project.get("workspace_aliases", [])
        raw_primaries = raw_project.get("primary_workspaces", {})
        if not isinstance(raw_repositories, list) or not isinstance(raw_aliases, list):
            raise ValueError("project repositories and workspace_aliases must be lists")
        if not isinstance(raw_primaries, Mapping):
            raise ValueError("project primary_workspaces must be an object")

        repositories = []
        for raw_repository in raw_repositories:
            repository_id = _normalize_repository_id(raw_repository)
            owner = repository_owners.get(repository_id)
            if strict and owner and owner != project_id:
                raise ValueError(
                    f"repository {repository_id} belongs to multiple projects"
                )
            repository_owners.setdefault(repository_id, project_id)
            if repository_id not in repositories:
                repositories.append(repository_id)

        aliases = []
        for raw_alias in raw_aliases:
            alias = canonicalize_workspace_path(raw_alias)
            key = alias.casefold()
            owner = alias_owners.get(key)
            if strict and owner and owner != project_id:
                raise ValueError(f"workspace alias {alias} belongs to multiple projects")
            alias_owners.setdefault(key, project_id)
            if alias not in aliases:
                aliases.append(alias)

        primaries = {}
        for raw_repository, raw_path in raw_primaries.items():
            repository_id = _normalize_repository_id(raw_repository)
            if repository_id not in repositories:
                raise ValueError("primary workspace repository must belong to its project")
            primaries[repository_id] = canonicalize_workspace_path(raw_path)

        normalized_projects.append(
            {
                "id": project_id,
                "name": normalized_name,
                "repositories": repositories,
                "primary_workspaces": primaries,
                "workspace_aliases": aliases,
            }
        )
    return {"version": 1, "projects": normalized_projects}


def load_identity_config(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return json.loads(json.dumps(EMPTY_CONFIG))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("identity configuration is not valid JSON") from error
    return _normalize_config(payload, strict=True)


def save_identity_config(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(path)
    normalized = _normalize_config(payload, strict=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return normalized


def _run_git(path: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", path, *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return result.stdout.strip()


class ProjectIdentityResolver:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        git_runner: Callable[..., str] = _run_git,
    ):
        self.config = _normalize_config(config, strict=False)
        self.git_runner = git_runner
        self._cache: dict[str, dict[str, Any]] = {}
        self._repositories: dict[str, list[dict[str, Any]]] = {}
        self._aliases: dict[str, list[dict[str, Any]]] = {}
        self._folded_aliases: dict[str, list[dict[str, Any]]] = {}
        for project in self.config["projects"]:
            for repository_id in project["repositories"]:
                self._repositories.setdefault(repository_id, []).append(project)
            for alias in project["workspace_aliases"]:
                self._aliases.setdefault(alias, []).append(project)
                self._folded_aliases.setdefault(alias.casefold(), []).append(project)

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.config, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _live_repository(self, workspace_id: str) -> tuple[Optional[str], str]:
        path = Path(workspace_id)
        if not path.is_dir():
            return None, path.name or "Workspace"
        try:
            common = self.git_runner(
                workspace_id,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return None, path.name or "Workspace"
        common_id = canonicalize_workspace_path(common)
        repository_name = Path(common_id).parent.name or path.name or "Repository"
        try:
            remotes = [
                item.strip()
                for item in self.git_runner(workspace_id, "remote").splitlines()
                if item.strip()
            ]
        except (OSError, ValueError, subprocess.SubprocessError):
            remotes = []
        ordered = (["origin"] if "origin" in remotes else []) + sorted(
            item for item in remotes if item != "origin"
        )
        for remote in ordered:
            try:
                raw_url = self.git_runner(workspace_id, "remote", "get-url", remote)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            normalized = normalize_remote_url(raw_url)
            if normalized:
                return "remote:" + normalized, repository_name
        return "gitdir:" + common_id, repository_name

    @staticmethod
    def _workspace_repository_id(workspace_id: str) -> str:
        digest = hashlib.sha256(workspace_id.casefold().encode("utf-8")).hexdigest()[:20]
        return "workspace:" + digest

    @staticmethod
    def _unattributed(
        workspace_id: str,
        workspace_name: str,
        repository_id: str,
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "repository_id": repository_id,
            "repository_name": workspace_name,
            "project_id": "unattributed",
            "project_name": "Unattributed",
            "identity_confidence": "unattributed",
            "identity_warnings": warnings,
        }

    def resolve(self, workspace_path: str) -> dict[str, Any]:
        workspace_id = canonicalize_workspace_path(workspace_path)
        cached = self._cache.get(workspace_id)
        if cached is not None:
            return dict(cached)
        workspace_name = Path(workspace_id).name or workspace_id
        repository_id, repository_name = self._live_repository(workspace_id)

        if repository_id:
            projects = self._repositories.get(repository_id, [])
            if len(projects) > 1:
                result = self._unattributed(
                    workspace_id,
                    workspace_name,
                    repository_id,
                    ["conflicting_repository_membership"],
                )
            elif len(projects) == 1:
                project = projects[0]
                result = {
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                    "repository_id": repository_id,
                    "repository_name": repository_name,
                    "project_id": project["id"],
                    "project_name": project["name"],
                    "identity_confidence": "configured",
                    "identity_warnings": [],
                }
            else:
                result = {
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                    "repository_id": repository_id,
                    "repository_name": repository_name,
                    "project_id": repository_id,
                    "project_name": repository_name,
                    "identity_confidence": "git",
                    "identity_warnings": [],
                }
        else:
            exact = self._aliases.get(workspace_id, [])
            projects = exact or self._folded_aliases.get(workspace_id.casefold(), [])
            unique = {project["id"]: project for project in projects}
            if len(unique) > 1:
                result = self._unattributed(
                    workspace_id,
                    workspace_name,
                    self._workspace_repository_id(workspace_id),
                    ["conflicting_workspace_alias"],
                )
            elif len(unique) == 1:
                project = next(iter(unique.values()))
                project_repositories = project["repositories"]
                alias_repository = (
                    project_repositories[0]
                    if len(project_repositories) == 1
                    else self._workspace_repository_id(workspace_id)
                )
                result = {
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                    "repository_id": alias_repository,
                    "repository_name": project["name"],
                    "project_id": project["id"],
                    "project_name": project["name"],
                    "identity_confidence": "configured",
                    "identity_warnings": [],
                }
            elif Path(workspace_id).exists():
                path_repository = self._workspace_repository_id(workspace_id)
                result = {
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                    "repository_id": path_repository,
                    "repository_name": workspace_name,
                    "project_id": "path:" + path_repository.split(":", 1)[1],
                    "project_name": workspace_name,
                    "identity_confidence": "path",
                    "identity_warnings": ["not_a_git_repository"],
                }
            else:
                result = self._unattributed(
                    workspace_id,
                    workspace_name,
                    self._workspace_repository_id(workspace_id),
                    ["workspace_missing"],
                )
        self._cache[workspace_id] = result
        return dict(result)
