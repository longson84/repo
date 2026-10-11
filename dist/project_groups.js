'use strict';

function projectNameError(projects, name, exceptId = '') {
  const normalized = String(name ?? '').trim();
  if (!normalized) return 'Project name is required.';
  const duplicate = (projects || []).some(
    (project) =>
      project.id !== exceptId &&
      String(project.name || '')
        .trim()
        .toLocaleLowerCase() === normalized.toLocaleLowerCase(),
  );
  return duplicate ? 'Project name already exists.' : null;
}

function slugProjectId(name, projects) {
  const base =
    String(name ?? '')
      .trim()
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-')
      .replace(/^[._-]+|[._-]+$/g, '') || 'project';
  const used = new Set((projects || []).map((project) => project.id));
  if (!used.has(base)) return base;
  let suffix = 2;
  while (used.has(`${base}-${suffix}`)) suffix += 1;
  return `${base}-${suffix}`;
}

function cloneProjectConfig(config) {
  const cloned = JSON.parse(JSON.stringify(config || { version: 1, projects: [] }));
  cloned.version = 1;
  cloned.projects = (cloned.projects || []).map((project) => ({
    ...project,
    repositories: Array.isArray(project.repositories) ? project.repositories : [],
    primary_workspaces:
      project.primary_workspaces && typeof project.primary_workspaces === 'object'
        ? project.primary_workspaces
        : {},
    workspace_aliases: Array.isArray(project.workspace_aliases) ? project.workspace_aliases : [],
  }));
  return cloned;
}

function uniqueRepositoryIds(repositoryIds) {
  return [...new Set((repositoryIds || []).filter((repositoryId) => typeof repositoryId === 'string'))];
}

function removeRepositoryMemberships(config, repositoryIds) {
  const selected = new Set(uniqueRepositoryIds(repositoryIds));
  const movedPrimaries = {};
  for (const project of config.projects) {
    project.repositories = project.repositories.filter((repositoryId) => !selected.has(repositoryId));
    for (const repositoryId of selected) {
      if (Object.prototype.hasOwnProperty.call(project.primary_workspaces, repositoryId)) {
        movedPrimaries[repositoryId] = project.primary_workspaces[repositoryId];
        delete project.primary_workspaces[repositoryId];
      }
    }
  }
  return movedPrimaries;
}

function assignRepositories(config, repositoryIds, target) {
  const next = cloneProjectConfig(config);
  const selected = uniqueRepositoryIds(repositoryIds);
  if (!selected.length) throw new Error('Select at least one repository.');

  let projectId = target && target.projectId;
  let project = projectId ? next.projects.find((item) => item.id === projectId) : null;
  if (projectId && !project) throw new Error('Project no longer exists.');
  if (!project) {
    const name = String((target && target.name) || '').trim();
    const error = projectNameError(next.projects, name);
    if (error) throw new Error(error);
    projectId = slugProjectId(name, next.projects);
    project = {
      id: projectId,
      name,
      repositories: [],
      primary_workspaces: {},
      workspace_aliases: [],
    };
    next.projects.push(project);
  }

  const movedPrimaries = removeRepositoryMemberships(next, selected);
  project.repositories.push(...selected);
  for (const repositoryId of selected) {
    if (Object.prototype.hasOwnProperty.call(movedPrimaries, repositoryId)) {
      project.primary_workspaces[repositoryId] = movedPrimaries[repositoryId];
    }
  }
  return { config: next, projectId };
}

function makeRepositoriesStandalone(config, repositoryIds) {
  const next = cloneProjectConfig(config);
  removeRepositoryMemberships(next, repositoryIds);
  next.projects = next.projects.filter(
    (project) =>
      project.repositories.length ||
      project.workspace_aliases.length ||
      Object.keys(project.primary_workspaces).length,
  );
  return next;
}

function renameConfiguredProject(config, projectId, name) {
  const next = cloneProjectConfig(config);
  const project = next.projects.find((item) => item.id === projectId);
  if (!project) throw new Error('Project no longer exists.');
  const error = projectNameError(next.projects, name, projectId);
  if (error) throw new Error(error);
  project.name = String(name).trim();
  return next;
}

function deleteConfiguredProject(config, projectId) {
  const next = cloneProjectConfig(config);
  const project = next.projects.find((item) => item.id === projectId);
  if (!project) throw new Error('Project no longer exists.');
  if (project.workspace_aliases.length || Object.keys(project.primary_workspaces).length) {
    throw new Error('Historical aliases or primary workspaces must be preserved.');
  }
  next.projects = next.projects.filter((item) => item.id !== projectId);
  return next;
}
