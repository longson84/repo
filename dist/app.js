'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) =>
  String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
let repos = [],
  machineName = 'This machine',
  scannedAt = null,
  filter = 'all',
  paired = false,
  roots = [],
  paths = [],
  workspaces = [],
  token = '',
  lastFocus,
  activity = [],
  busy = false,
  statsBusy = false,
  statsByPath = {},
  statsProjects = [],
  gitStatsInvalidatedByIdentity = false,
  detailPath = null,
  detailPeriod = 'day',
  detailCursor = null,
  statsPeriod = 'day',
  statsCursor = null,
  boardSortKey = 'commits30d',
  boardSortDir = -1,
  locSortKey = 'total',
  locSortDir = -1,
  locMetric = 'code',
  sortKey = 'name',
  sortDir = 1;
const statsExpandedProjects = new Set();
const statsExpandedRepositories = new Set();
const projectManagerDisabledState = new WeakMap();
let drawerCloseHandler = null;
const base = 'http://127.0.0.1:48731';
const TOKEN_KEY = 'repo.companion.token';
function readStoredToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}
function writeStoredToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode */
  }
}
function clearPairing() {
  token = '';
  paired = false;
  machineName = 'This machine';
  roots = [];
  paths = [];
  workspaces = [];
  repos = [];
  scannedAt = null;
  statsByPath = {};
  statsProjects = [];
  gitStatsInvalidatedByIdentity = false;
  writeStoredToken('');
}
function trackingReconciliationMessage(reconciliation) {
  if (!reconciliation) return '';
  const labels = [
    ['removed_paths', 'saved path', 'saved paths'],
    ['removed_repository_memberships', 'repository membership', 'repository memberships'],
    ['removed_primary_workspaces', 'primary workspace', 'primary workspaces'],
    ['removed_workspace_aliases', 'workspace alias', 'workspace aliases'],
    ['removed_projects', 'empty project', 'empty projects'],
  ];
  const removed = labels
    .map(([key, singular, plural]) => {
      const count = Number(reconciliation[key] || 0);
      return count ? `${count} ${count === 1 ? singular : plural}` : '';
    })
    .filter(Boolean);
  if (!removed.length) return '';
  return `Removed ${removed.join(', ')} outside Selected GitHub repositories.`;
}
function applyStatus(result) {
  machineName = result.machine || machineName;
  roots = result.roots || [];
  paths = result.paths || [];
  if (Array.isArray(result.workspaces)) workspaces = result.workspaces;
  if (Array.isArray(result.repositories)) repos = result.repositories;
  if ('scannedAt' in result) scannedAt = result.scannedAt || null;
  const reconciliationMessage = trackingReconciliationMessage(result.reconciliation);
  if (reconciliationMessage) {
    activity.unshift(reconciliationMessage);
    toast(reconciliationMessage);
  }
}
function invalidateTrackedScopeData() {
  statsByPath = {};
  statsProjects = [];
  statsExpandedProjects.clear();
  statsExpandedRepositories.clear();
  aiSessions = null;
  aiSearch = null;
  aiUsage = null;
  aiUsagePeriodKey = null;
  aiUsageExpandedProjects.clear();
  aiUsageExpandedModels.clear();
}
async function pairWithToken(candidate) {
  const previous = token;
  token = candidate;
  try {
    const result = await api('status');
    paired = true;
    applyStatus(result);
    writeStoredToken(candidate);
    return result;
  } catch (error) {
    token = previous;
    throw error;
  }
}
async function api(path, body) {
  const controller = new AbortController();
  const long =
    path === 'scan' || path === 'ai/detect' || path === 'ai/sessions' || (path === 'stats' && body);
  const timer = setTimeout(() => controller.abort(), long ? 900000 : 20000);
  try {
    const response = await fetch(base + '/api/' + path, {
      method: body ? 'POST' : 'GET',
      headers: {
        Authorization: 'Bearer ' + token,
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) {
      const error = Error(result.error || 'Companion request failed.');
      error.status = response.status;
      error.code = result.code || null;
      throw error;
    }
    return result;
  } finally {
    clearTimeout(timer);
  }
}
function mergeStats(list, projects) {
  (list || []).forEach((s) => {
    if (s?.path) statsByPath[s.path] = s;
  });
  if (Array.isArray(projects)) statsProjects = projects;
}
function emptyLines() {
  return { additions: 0, deletions: 0, net: 0 };
}
function addLines(bucket, piece) {
  bucket.additions += piece?.additions || 0;
  bucket.deletions += piece?.deletions || 0;
  bucket.net += piece?.net != null ? piece.net : (piece?.additions || 0) - (piece?.deletions || 0);
  return bucket;
}
function formatCompact(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const value = Math.round(Number(n));
  const body = Math.abs(value).toLocaleString('en-US');
  return (value < 0 ? '−' : '') + body;
}
/** 1,230,050 → 1,230k */
function formatKilo(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const value = Math.round(Number(n));
  if (value === 0) return '0';
  const abs = Math.abs(value);
  if (abs < 1000) return (value < 0 ? '−' : '') + String(abs);
  return (value < 0 ? '−' : '') + Math.round(abs / 1000).toLocaleString('en-US') + 'k';
}
function locMetricField() {
  return locMetric === 'complexity' ? 'complexity' : 'code';
}
function formatLocCell(n) {
  if (locMetric === 'complexity') return formatCompact(n);
  return formatKilo(n);
}
function locLanguageMatrix() {
  const field = locMetricField();
  const totals = new Map();
  const rows = paths.map((path) => {
    const s = statsByPath[path];
    const byLang = {};
    (s?.locByLanguage || []).forEach((lang) => {
      const name = lang?.name;
      const value = lang?.[field] || 0;
      if (!name || !value) return;
      byLang[name] = value;
      totals.set(name, (totals.get(name) || 0) + value);
    });
    return {
      path,
      name: s?.name || path.split(/[\\/]/).pop(),
      byLang,
      total: Object.values(byLang).reduce((sum, n) => sum + n, 0),
      hasLoc: s?.locCode != null,
    };
  });
  const languages = [...totals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name]) => name);
  rows.sort((a, b) => {
    if (locSortKey === 'name') {
      const cmp = a.name.localeCompare(b.name);
      return locSortDir > 0 ? cmp : -cmp;
    }
    const av = locSortKey === 'total' ? a.total : a.byLang[locSortKey] || 0;
    const bv = locSortKey === 'total' ? b.total : b.byLang[locSortKey] || 0;
    if (av === bv) return a.name.localeCompare(b.name);
    return av < bv ? -locSortDir : locSortDir;
  });
  return { languages, rows };
}
function locSortClass(key) {
  if (locSortKey !== key) return 'sortable';
  return `sortable ${locSortDir > 0 ? 'sorted-asc' : 'sorted-desc'}`;
}
function locMatrixHtml() {
  const { languages, rows } = locLanguageMatrix();
  if (!languages.length) {
    return '<p class="note">No language matrix yet. Scan after scc is available.</p>';
  }
  const metricNote =
    locMetric === 'complexity'
      ? 'Cells are scc complexity estimates (not thousands).'
      : 'Code lines in thousands (1,230k = 1,230,000).';
  return /* HTML */ `
    <div class="board-wrap loc-matrix">
      <table>
        <thead>
          <tr>
            <th class="sticky-col ${locSortClass('name')}" data-loc-sort="name">Repo</th>
            ${languages
              .map(
                (lang) =>
                  `<th class="${locSortClass(lang)}" data-loc-sort="${esc(lang)}" title="${esc(lang)}">${esc(lang)}</th>`,
              )
              .join('')}
            <th class="${locSortClass('total')}" data-loc-sort="total">Total</th>
          </tr>
        </thead>
        <tbody>
          ${rows
            .map((row) => {
              const cells = languages
                .map((lang) => {
                  const value = row.byLang[lang];
                  if (!value) return '<td class="num muted">—</td>';
                  return `<td class="num" title="${esc(formatCompact(value))}">${esc(formatLocCell(value))}</td>`;
                })
                .join('');
              return /* HTML */ `
                <tr class="board-row" data-path="${esc(row.path)}" tabindex="0">
                  <td class="sticky-col"><strong>${esc(row.name)}</strong></td>
                  ${cells}
                  <td
                    class="num ${row.total ? '' : 'muted'}"
                    title="${esc(formatCompact(row.total))}"
                  >
                    ${esc(row.total ? formatLocCell(row.total) : '—')}
                  </td>
                </tr>
              `;
            })
            .join('')}
        </tbody>
      </table>
    </div>
    <p class="note">
      Via scc (code excludes comments). Skips docs/config/markup noise, TypeScript typings, and
      other non-app languages. ${metricNote} Click a column header to sort.
    </p>
  `;
}
function formatLines(bucket) {
  if (!bucket || (bucket.additions == null && bucket.deletions == null)) return '—';
  const additions = bucket.additions || 0;
  const deletions = bucket.deletions || 0;
  const net = bucket.net != null ? bucket.net : additions - deletions;
  const netLabel = net > 0 ? `+${formatCompact(net)}` : formatCompact(net);
  return `+${formatCompact(additions)} / −${formatCompact(deletions)} / ${netLabel}`;
}
function sumLinesRange(map, start, days) {
  const total = emptyLines();
  for (let i = 0; i < days; i += 1) {
    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    addLines(total, map[dateKey(day)]);
  }
  return total;
}
function linesForBucket(map, item, mode) {
  if (!map) return emptyLines();
  if (mode === 'day') return addLines(emptyLines(), map[item.period]);
  if (mode === 'week' && item.start) return sumLinesRange(map, item.start, 7);
  if (mode === 'month' && /^\d{4}-\d{2}$/.test(item.period)) {
    const [y, m] = item.period.split('-').map(Number);
    const start = new Date(y, m - 1, 1);
    return sumLinesRange(map, start, new Date(y, m, 0).getDate());
  }
  if (mode === 'year' && /^\d{4}$/.test(String(item.period))) {
    const total = emptyLines();
    Object.entries(map).forEach(([day, piece]) => {
      if (day.startsWith(String(item.period))) addLines(total, piece);
    });
    return total;
  }
  return emptyLines();
}
function formatBytes(n) {
  if (n == null || Number.isNaN(n)) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = n,
    i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return (i ? value.toFixed(1) : String(value)) + ' ' + units[i];
}
function relativeTime(iso) {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '—';
  const mins = Math.round(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hours = Math.round(mins / 60);
  if (hours < 48) return hours + 'h ago';
  const days = Math.round(hours / 24);
  if (days < 60) return days + 'd ago';
  return new Date(iso).toLocaleDateString();
}
function formatDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  const dd = String(date.getDate()).padStart(2, '0');
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const yy = String(date.getFullYear()).slice(-2);
  return `${dd}/${mm}/${yy}`;
}
function sortValue(r, key) {
  const s = statsByPath[r.path] || {};
  switch (key) {
    case 'name':
      return (r.name || '').toLowerCase();
    case 'branch':
      return (r.branch || '').toLowerCase();
    case 'first':
      return s.firstCommitAt ? new Date(s.firstCommitAt).getTime() : null;
    case 'age':
      return s.ageDays ?? null;
    case 'commits':
      return s.commitCount ?? null;
    case 'd30':
      return s.commits30d ?? null;
    case 'd7':
      return s.commits7d ?? null;
    case 'last':
      return s.lastCommitAt ? new Date(s.lastCommitAt).getTime() : null;
    case 'behind':
      return r.behind ?? null;
    case 'ahead':
      return r.ahead ?? null;
    case 'dirty':
      return (r.modified || 0) + (r.untracked || 0);
    case 'next':
      return next(r).toLowerCase();
    default:
      return null;
  }
}
function compareSort(a, b) {
  const left = sortValue(a, sortKey);
  const right = sortValue(b, sortKey);
  if (left == null && right == null) return a.name.localeCompare(b.name);
  if (left == null) return 1;
  if (right == null) return -1;
  if (typeof left === 'string' || typeof right === 'string') {
    return String(left).localeCompare(String(right)) * sortDir || a.name.localeCompare(b.name);
  }
  return (left < right ? -1 : left > right ? 1 : 0) * sortDir || a.name.localeCompare(b.name);
}
function paintSortHeaders() {
  document.querySelectorAll('#repoHead th.sortable').forEach((th) => {
    const active = th.dataset.sort === sortKey;
    th.setAttribute('aria-sort', active ? (sortDir > 0 ? 'ascending' : 'descending') : 'none');
  });
}
function sparkline(weeks) {
  const values = Array.isArray(weeks) ? weeks : [];
  const max = Math.max(1, ...values);
  return /* HTML */ `
    <div class="sparkline" aria-hidden="true">
      ${values
        .map((v) => `<span style="height:${Math.max(2, Math.round((v / max) * 36))}px"></span>`)
        .join('')}
    </div>
  `;
}
function portfolioNumbers() {
  if (statsProjects.length) {
    const repositories = statsProjects.flatMap((project) => project.repositories || []);
    const names = new Set();
    statsProjects.forEach((project) =>
      (project.topContributors || []).forEach((contributor) => names.add(contributor.name)),
    );
    return {
      cached: repositories.length,
      tracked: paths.length,
      commits: statsProjects.reduce((sum, project) => sum + (project.commitCount || 0), 0),
      commits30: statsProjects.reduce((sum, project) => sum + (project.commits30d || 0), 0),
      contributors: names.size,
    };
  }
  const cached = paths.map((p) => statsByPath[p]).filter(Boolean);
  const names = new Set();
  cached.forEach((s) => (s.topContributors || []).forEach((c) => names.add(c.name)));
  return {
    cached: cached.length,
    tracked: paths.length,
    commits: cached.reduce((sum, s) => sum + (s.commitCount || 0), 0),
    commits30: cached.reduce((sum, s) => sum + (s.commits30d || 0), 0),
    contributors: names.size,
  };
}
async function hydrateStats() {
  if (!paired) return;
  try {
    const result = await api('stats');
    mergeStats(result.repositories, result.projects);
    render();
  } catch (_) {
    /* cache hydrate is best-effort */
  }
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').style.display = 'block';
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => ($('toast').style.display = 'none'), 6000);
}
function open(body, label) {
  drawerCloseHandler = null;
  $('drawerBody').classList.remove('project-manager-body');
  document.querySelector('.drawer')?.classList.remove('project-manager-drawer');
  lastFocus = document.activeElement;
  $('drawerLabel').textContent = label;
  $('drawerBody').innerHTML = body;
  $('overlay').classList.add('open');
  $('close').focus();
}
function close() {
  if (drawerCloseHandler && drawerCloseHandler() === false) return;
  drawerCloseHandler = null;
  $('overlay').classList.remove('open');
  lastFocus?.focus();
}
function eligible(r) {
  return (
    r.fresh &&
    !r.error &&
    r.upstream &&
    r.ahead === 0 &&
    r.behind > 0 &&
    r.modified + r.untracked === 0
  );
}
function next(r) {
  if (r.error) return 'Check Git access';
  if (r.ahead > 0 && r.behind > 0) return 'Diverged · review';
  if (r.modified + r.untracked) return 'Review local work';
  if (eligible(r)) return 'Ready to pull';
  if (r.ahead > 0) return 'Ready to publish';
  return r.fresh ? 'Up to date' : 'Not fetched';
}
function render() {
  const list = repos
    .filter(
      (r) =>
        r.name.toLowerCase().includes($('search').value.toLowerCase()) &&
        (filter === 'all' ||
          (filter === 'behind' && r.behind > 0) ||
          (filter === 'ahead' && r.ahead > 0) ||
          (filter === 'dirty' && r.modified + r.untracked > 0)),
    )
    .sort(compareSort);
  paintSortHeaders();
  $('rows').innerHTML = list
    .map((r) => {
      const s = statsByPath[r.path];
      const dirty = (r.modified || 0) + (r.untracked || 0);
      return /* HTML */ `
        <tr class="repo-row" data-path="${esc(r.path)}" tabindex="0">
          <td>
            <span class="repo-name">${esc(r.name)}</span>
            <span class="repo-path">${esc(r.path)}</span>
          </td>
          <td><span class="branch">⑂ ${esc(r.branch || 'Unknown')}</span></td>
          <td>${esc(formatDate(s?.firstCommitAt))}</td>
          <td>
            <span class="count ${s?.ageDays != null ? '' : 'zero'}">${s?.ageDays ?? '—'}</span>
          </td>
          <td>
            <span class="count ${s?.commitCount ? '' : 'zero'}">${s?.commitCount ?? '—'}</span>
          </td>
          <td>
            <span class="count ${s?.commits30d ? '' : 'zero'}">${s?.commits30d ?? '—'}</span>
          </td>
          <td>
            <span class="count ${s?.commits7d ? '' : 'zero'}">${s?.commits7d ?? '—'}</span>
          </td>
          <td>${esc(formatDate(s?.lastCommitAt))}</td>
          <td>
            <span class="count ${r.behind ? 'behind' : 'zero'}">${r.behind ?? '?'}</span>
          </td>
          <td>
            <span class="count ${r.ahead ? 'ahead' : 'zero'}">${r.ahead ?? '?'}</span>
          </td>
          <td>${dirty ? `${dirty} ${dirty === 1 ? 'file' : 'files'}` : '✓ Clean'}</td>
          <td>
            <span class="badge ${eligible(r) || next(r) === 'Up to date' ? 'pull' : 'review'}">
              ${esc(next(r))}
            </span>
          </td>
        </tr>
      `;
    })
    .join('');
  $('empty').hidden = !!list.length;
  $('empty').textContent = !paired
    ? 'Connect the local companion to begin.'
    : !scannedAt
      ? 'Select a repository with a github.com remote under Manage tracked repos, then scan this machine.'
      : 'No repositories match this view.';
  $('repoCount').textContent = list.length;
  ['total', 'behindStat', 'aheadStat', 'dirtyStat'].forEach(
    (id, i) =>
      ($(id).textContent = !scannedAt
        ? '—'
        : [
            repos.length,
            repos.filter((r) => r.behind > 0).length,
            repos.filter((r) => r.ahead > 0).length,
            repos.filter((r) => r.modified + r.untracked > 0).length,
          ][i]),
  );
  const pulse = portfolioNumbers();
  const showPortfolio = paired && paths.length;
  $('portfolioStats').hidden = !showPortfolio;
  if (showPortfolio) {
    const hasCache = pulse.cached > 0;
    $('commitStat').textContent = hasCache ? String(pulse.commits) : '—';
    $('commit30Stat').textContent = hasCache ? String(pulse.commits30) : '—';
    $('contributorStat').textContent = hasCache ? String(pulse.contributors) : '—';
    $('statsCoverageStat').textContent = `${pulse.cached}/${pulse.tracked}`;
    $('statsCoverageLabel').textContent = hasCache
      ? 'Cached / tracked'
      : gitStatsInvalidatedByIdentity
        ? 'Grouping changed · Scan to rebuild'
        : statsBusy || busy
          ? 'Scanning…'
          : 'Run Scan to fill stats';
  }
  $('machineTitle').innerHTML =
    esc(machineName) + ` <span>${paired ? 'CONNECTED' : 'NOT CONNECTED'}</span>`;
  $('machineMeta').textContent = paired
    ? `${paths.length} selected GitHub repos · ${scannedAt ? 'Scanned ' + new Date(scannedAt).toLocaleString() : 'Not scanned yet'}`
    : 'Run companion with --ui, or connect with a pairing token.';
  $('snapshotLabel').textContent = scannedAt
    ? 'Compared with each branch’s upstream · ' + new Date(scannedAt).toLocaleString()
    : 'No live scan yet';
  $('currentInsights').hidden = !scannedAt;
  $('scan').disabled = !paired || busy || statsBusy || !paths.length;
  $('manage').disabled = !paired || busy || statsBusy;
  $('connect').textContent = paired ? 'Connection settings' : 'Connect companion';
  if ($('disconnect')) $('disconnect').hidden = !paired;
  $('scan').innerHTML = busy || statsBusy ? 'Scanning…' : '<span class="arrow">⟳</span> Scan';
  $('currentMachineNav').textContent = machineName;
  $('currentMachineState').textContent = paired ? 'Companion connected' : 'Not connected';
  $('nextTitle').textContent = `${repos.filter(eligible).length} repositories can receive updates`;
  $('nextText').textContent =
    'Review the eligible branches and the reasons other repositories need attention. This version previews updates; it does not pull or push.';
  const route = parseRoute();
  const onDetail = route.view === 'repo';
  const onStats = route.view === 'stats';
  const onLanguages = route.view === 'languages';
  const onAi = route.view === 'ai';
  $('overviewView').hidden = onDetail || onStats || onLanguages || onAi;
  $('detailView').hidden = !onDetail;
  $('statsView').hidden = !onStats;
  $('languagesView').hidden = !onLanguages;
  $('aiView').hidden = !onAi;
  document.querySelectorAll('.nav button').forEach((button) => {
    button.classList.toggle(
      'active',
      (button.id === 'overviewNav' && route.view === 'overview') ||
        (button.id === 'statsNav' && route.view === 'stats') ||
        (button.id === 'languagesNav' && route.view === 'languages') ||
        (button.id === 'aiNav' && route.view === 'ai'),
    );
  });
  if (onDetail) {
    detailPath = route.path;
    paintDetail();
    $('crumb').innerHTML =
      `Theana <span style="padding:0 10px">/</span> <a href="#/" style="color:inherit">Repositories</a> <span style="padding:0 10px">/</span> <b>${esc(
        (
          repos.find((r) => r.path === detailPath) ||
          statsByPath[detailPath] || { name: detailPath.split(/[\\/]/).pop() }
        ).name,
      )}</b>`;
  } else if (onStats) {
    paintStats();
    $('crumb').innerHTML =
      'Theana <span style="padding:0 10px">/</span> <a href="#/" style="color:inherit">Repositories</a> <span style="padding:0 10px">/</span> <b>Stats</b>';
  } else if (onLanguages) {
    paintLanguages();
    $('crumb').innerHTML =
      'Theana <span style="padding:0 10px">/</span> <a href="#/" style="color:inherit">Repositories</a> <span style="padding:0 10px">/</span> <b>Languages</b>';
  } else if (onAi) {
    paintAi();
    $('crumb').innerHTML = 'Theana <span style="padding:0 10px">/</span> <b>AI</b>';
  } else {
    detailPath = null;
    $('crumb').innerHTML =
      'Theana <span style="padding: 0 10px">/</span> <b>Repository workspace</b>';
  }
  document.querySelectorAll('tr.repo-row').forEach((row) => {
    row.onclick = () => openRepo(row.dataset.path);
    row.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openRepo(row.dataset.path);
      }
    };
  });
}
function cells(pairs) {
  return pairs
    .map(
      ([label, value]) => /* HTML */ `
        <div class="detail-cell">
          <span class="small">${label}</span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join('');
}
function parseRoute() {
  const raw = location.hash.replace(/^#/, '');
  if (raw.startsWith('/repo?')) {
    return { view: 'repo', path: decodeURIComponent(raw.slice('/repo?'.length)) };
  }
  if (raw === '/stats' || raw.startsWith('/stats?')) return { view: 'stats' };
  if (raw === '/languages' || raw.startsWith('/languages?')) return { view: 'languages' };
  if (raw === '/ai' || raw.startsWith('/ai?') || raw.startsWith('/ai/')) return { view: 'ai' };
  return { view: 'overview' };
}
function parseHash() {
  const route = parseRoute();
  return route.view === 'repo' ? route.path : null;
}
function openRepo(path) {
  if (!path) return;
  detailPeriod = 'day';
  detailCursor = null;
  location.hash = '#/repo?' + encodeURIComponent(path);
}
function closeRepo() {
  location.hash = '#/';
}
function openStats() {
  location.hash = '#/stats';
}
function openLanguages() {
  location.hash = '#/languages';
}
function openAi() {
  location.hash = '#/ai';
}

let aiDetect = null;
let aiSessions = null;
let aiUsage = null;
let aiUsageInvalidatedByIdentity = false;
let aiSearch = null;
let aiBusy = false;
let aiTab = 'detect'; // detect | sessions | tokens | search
let aiSessionFilter = {
  source: '',
  project: '',
};
let aiUsageFilter = {
  source: '',
};
let aiUsageGroup = 'project'; // model | project | period | surface | tool
let aiUsagePeriod = 'month'; // day | week | month | year
let aiUsagePeriodKey = null; // selected period key within aiUsagePeriod
let aiUsageProjectFilter = '';
let aiUsageModelFilter = '';
let aiUsageSortKey = null;
let aiUsageSortDir = 1;
const aiUsageExpandedProjects = new Set();
const aiUsageExpandedModels = new Set();
let aiSearchFilter = {
  q: '',
  source: '',
};

function formatBytes(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  let value = Number(n);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  for (const unit of units) {
    if (value < 1024 || unit === 'TB') {
      return unit === 'B' ? `${Math.round(value)} ${unit}` : `${value.toFixed(1)} ${unit}`;
    }
    value /= 1024;
  }
  return `${value.toFixed(1)} TB`;
}

function formatAiWhen(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

async function hydrateAiDetect() {
  if (!paired) {
    aiDetect = null;
    return;
  }
  try {
    aiDetect = await api('ai/detect');
  } catch {
    aiDetect = null;
  }
}

async function runAiDetect() {
  if (!paired || aiBusy) return;
  aiBusy = true;
  paintAi();
  try {
    aiDetect = await api('ai/detect', {});
    toast('AI stores detected on this machine.');
  } catch (error) {
    toast(error.message || 'Detect failed.');
  } finally {
    aiBusy = false;
    paintAi();
  }
}

async function runAiSessions() {
  if (!paired || aiBusy) return;
  aiBusy = true;
  paintAi();
  try {
    const body = { limit: 200 };
    if (aiSessionFilter.source) body.sources = [aiSessionFilter.source];
    if (aiSessionFilter.project.trim()) body.project = aiSessionFilter.project.trim();
    aiSessions = await api('ai/sessions', body);
    toast(`Loaded ${aiSessions.conversation_count ?? aiSessions.count ?? 0} conversations.`);
  } catch (error) {
    toast(error.message || 'Conversation loading failed.');
  } finally {
    aiBusy = false;
    paintAi();
  }
}

async function runAiUsage() {
  if (!paired || aiBusy) return;
  aiBusy = true;
  paintAi();
  try {
    const body = {};
    if (aiUsageFilter.source) body.sources = [aiUsageFilter.source];
    aiUsage = await api('ai/usage', body);
    aiUsageInvalidatedByIdentity = false;
    toast('Usage aggregated.');
  } catch (error) {
    toast(error.message || 'Usage failed.');
  } finally {
    aiBusy = false;
    paintAi();
  }
}

async function runAiSearch() {
  if (!paired || aiBusy) return;
  const q = (aiSearchFilter.q || '').trim();
  if (!q) {
    toast('Enter a search query.');
    return;
  }
  aiBusy = true;
  paintAi();
  try {
    const body = { q, limit: 50 };
    if (aiSearchFilter.source) body.sources = [aiSearchFilter.source];
    aiSearch = await api('ai/search', body);
    toast(
      `Found ${aiSearch.count} hit${aiSearch.count === 1 ? '' : 's'}${aiSearch.truncated ? ' (truncated)' : ''}.`,
    );
  } catch (error) {
    toast(error.message || 'Search failed.');
  } finally {
    aiBusy = false;
    paintAi();
  }
}

function formatTokens(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const value = Number(n);
  if (value >= 1_000_000) {
    return `${Math.round(value / 1_000_000).toLocaleString('en-US')}M`;
  }
  if (value >= 1_000) {
    const thousands = value / 1_000;
    const text =
      thousands >= 100
        ? Math.round(thousands).toLocaleString('en-US')
        : thousands.toLocaleString('en-US', {
            maximumFractionDigits: 1,
            minimumFractionDigits: 0,
          });
    return `${text}k`;
  }
  return Math.round(value).toLocaleString('en-US');
}

function paintAiDetectPanel() {
  const sources = aiDetect?.sources || [];
  const summary = aiDetect?.summary || { found: 0, stores_found: 0, total: 0 };
  const detectedAt = aiDetect?.detectedAt ? new Date(aiDetect.detectedAt).toLocaleString() : null;
  const byTool = {};
  sources.forEach((item) => {
    (byTool[item.tool] ||= []).push(item);
  });
  const toolOrder = ['antigravity', 'claude-code', 'claude-desktop', 'codex', 'cursor'];
  const tools = [
    ...toolOrder.filter((t) => byTool[t]),
    ...Object.keys(byTool)
      .filter((t) => !toolOrder.includes(t))
      .sort(),
  ];
  let rows = '';
  if (!paired) {
    rows = `<tr><td colspan="5"><div class="callout">Connect the companion to detect AI discussion stores on this machine.</div></td></tr>`;
  } else if (!sources.length) {
    rows = `<tr><td colspan="5"><div class="callout">Click Detect to scan known Antigravity, Claude, Codex, and Cursor locations.</div></td></tr>`;
  } else {
    tools.forEach((tool) => {
      rows += `<tr class="ai-tool-head"><td colspan="5">${esc(tool)}</td></tr>`;
      byTool[tool].forEach((item) => {
        const status = item.exists
          ? `<span class="ai-found">FOUND</span>`
          : `<span class="ai-missing">—</span>`;
        rows += `<tr>
          <td>${esc(item.surface)}</td>
          <td>${status}</td>
          <td class="ai-path">${esc(item.path)}</td>
          <td>${item.exists ? esc(formatBytes(item.size_bytes)) : '—'}</td>
          <td class="small">${esc(item.notes || '')}</td>
        </tr>`;
      });
    });
  }
  return /* HTML */ `
    <div class="machine-meta" style="margin: 0 0 18px">
      ${detectedAt
        ? `${summary.stores_found ?? summary.found ?? 0}/${summary.total ?? sources.length} stores found · Detected ${esc(detectedAt)}`
        : paired
          ? 'Not detected yet on this machine.'
          : 'Companion not connected.'}
    </div>
    <table class="ai-table">
      <thead>
        <tr>
          <th>Surface</th>
          <th>Status</th>
          <th>Path</th>
          <th>Size</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function paintAiSessionsPanel() {
  const list = aiSessions?.sessions || [];
  const sourceOptions = [
    ['', 'All wave-1 sources'],
    ['antigravity.app', 'Antigravity app'],
    ['antigravity.ide', 'Antigravity IDE'],
    ['antigravity.cli', 'Antigravity CLI'],
    ['claude.code', 'Claude Code'],
    ['codex.home', 'Codex'],
    ['cursor.cli', 'Cursor CLI'],
    ['cursor.agent-transcripts', 'Cursor agent transcripts'],
  ]
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}" ${aiSessionFilter.source === value ? 'selected' : ''}>${esc(label)}</option>`,
    )
    .join('');
  let rows = '';
  if (!paired) {
    rows = `<tr><td colspan="7"><div class="callout">Connect the companion to list conversations.</div></td></tr>`;
  } else if (!aiSessions) {
    rows = `<tr><td colspan="7"><div class="callout">Click Load conversations to list Antigravity, Claude Code, Codex, and Cursor chats.</div></td></tr>`;
  } else if (!list.length) {
    rows = `<tr><td colspan="7"><div class="callout">No conversations matched these filters. Select a repository with a github.com remote.</div></td></tr>`;
  } else {
    list.forEach((item) => {
      rows += `<tr>
        <td class="small">${esc(formatAiWhen(item.updated_at || item.started_at))}</td>
        <td>${esc(item.tool)}<div class="small">${esc(item.surface)}</div></td>
        <td>${esc(item.project || '—')}</td>
        <td>${esc(item.preview || item.title || item.id)}</td>
        <td>${esc(String(item.execution_count ?? 1))}</td>
        <td>${esc(formatBytes(item.size_bytes))}</td>
        <td class="ai-path small">${esc(item.path || '')}</td>
      </tr>`;
    });
  }
  const meta = aiSessions
    ? `Selected GitHub repositories · ${aiSessions.conversation_count ?? aiSessions.count ?? 0} logical conversations · ${aiSessions.execution_count ?? '—'} executions · ${aiSessions.representation_count ?? '—'} physical representations${aiSessions.truncated ? ' (conversation list truncated)' : ''} · Listed ${esc(formatAiWhen(aiSessions.listedAt))}`
    : 'Selected GitHub repositories · Conversations not loaded yet.';
  return /* HTML */ `
    <div class="ai-filters">
      <label class="small" for="aiSourceFilter">Source</label>
      <select id="aiSourceFilter">
        ${sourceOptions}
      </select>
      <label class="small" for="aiProjectFilter">Project contains</label>
      <input
        id="aiProjectFilter"
        type="text"
        value="${esc(aiSessionFilter.project)}"
        placeholder="eravnTrans"
      />
      <button class="btn primary" id="aiSessionsBtn" ${!paired || aiBusy ? 'disabled' : ''}>
        ${aiBusy && aiTab === 'sessions'
          ? 'Loading…'
          : '<span class="arrow">⟳</span> Load conversations'}
      </button>
    </div>
    <div class="machine-meta" style="margin: 0 0 18px">${meta}</div>
    <table class="ai-table">
      <thead>
        <tr>
          <th>When</th>
          <th>Tool</th>
          <th>Project</th>
          <th>Preview</th>
          <th>Executions</th>
          <th>Size</th>
          <th>Path</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function aiPeriodSeriesKey() {
  if (aiUsagePeriod === 'week') return 'by_week';
  if (aiUsagePeriod === 'month') return 'by_month';
  if (aiUsagePeriod === 'year') return 'by_year';
  return 'by_day';
}

function aiPeriodSeries() {
  if (!aiUsage) return [];
  return aiUsage[aiPeriodSeriesKey()] || aiUsage.by_date || [];
}

function ensureAiPeriodKey() {
  const series = aiPeriodSeries();
  if (!series.length) {
    aiUsagePeriodKey = null;
    return null;
  }
  if (!aiUsagePeriodKey || !series.some((row) => row.period === aiUsagePeriodKey)) {
    aiUsagePeriodKey = series[0].period;
  }
  return aiUsagePeriodKey;
}

function formatAiPeriodLabel(period, grain = aiUsagePeriod) {
  if (!period) return '—';
  const value = String(period);
  if (grain === 'day' || /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value;
  }
  if (grain === 'week' || /^\d{4}-W\d{2}$/.test(value)) {
    return value.replace(/^(\d{4})-W(\d{2})$/, (_, y, w) => `W${w}/${y.slice(-2)}`);
  }
  if (grain === 'month' || /^\d{4}-\d{2}$/.test(value)) {
    const [y, m] = value.split('-');
    return y && m ? `${m}/${y}` : value;
  }
  return value;
}

function usageTokenVolume(item) {
  return ['input_tokens', 'cached_tokens', 'output_tokens', 'cache_write_tokens'].reduce(
    (sum, field) => sum + Number(item?.[field] || 0),
    0,
  );
}

function usageShare(item, denominator) {
  const total = usageTokenVolume(item);
  return denominator > 0 ? `${((total * 100) / denominator).toFixed(1)}%` : '—';
}

function usageCells(item, denominator) {
  return `
    <td>${esc(formatTokens(item.input_tokens))}</td>
    <td>${esc(formatTokens(item.cached_tokens))}</td>
    <td>${esc(formatTokens(item.output_tokens))}</td>
    <td>${esc(formatTokens(item.cache_write_tokens))}</td>
    <td>${esc(formatTokens(usageTokenVolume(item)))}</td>
    <td>${esc(usageShare(item, denominator))}</td>
    <td class="small">${esc(Number(item.events || 0).toLocaleString('en-US'))}</td>`;
}

function usageTotalRow(totals) {
  if (!totals) return '';
  return `<tr class="ai-total-row">
    <td>Total</td>
    ${usageCells(totals, usageTokenVolume(totals))}
  </tr>`;
}

function setAiUsageSort(key) {
  if (aiUsageSortKey === key) aiUsageSortDir = -aiUsageSortDir;
  else {
    aiUsageSortKey = key;
    aiUsageSortDir = key === 'label' ? 1 : -1;
  }
}

function sortAiUsageRows(list, labelFn) {
  const rows = [...(list || [])];
  if (!aiUsageSortKey) return rows;
  const numericValue = (item) => {
    if (aiUsageSortKey === 'total' || aiUsageSortKey === 'share') {
      return usageTokenVolume(item);
    }
    return Number(item?.[aiUsageSortKey] || 0);
  };
  return rows.sort((a, b) => {
    const aLabel = String(labelFn(a) || '');
    const bLabel = String(labelFn(b) || '');
    if (aiUsageSortKey === 'label') {
      return aLabel.localeCompare(bLabel) * aiUsageSortDir;
    }
    const difference = numericValue(a) - numericValue(b);
    return difference ? difference * aiUsageSortDir : aLabel.localeCompare(bLabel);
  });
}

function aiUsageHeaderCell(label, key) {
  const active = aiUsageSortKey === key;
  const direction = active ? (aiUsageSortDir > 0 ? 'ascending' : 'descending') : 'none';
  const arrow = active ? (aiUsageSortDir > 0 ? '↑' : '↓') : '';
  return `<th aria-sort="${direction}"><button type="button" class="ai-sort-button" data-ai-usage-sort="${esc(key)}">${esc(label)} <span aria-hidden="true">${arrow}</span></button></th>`;
}

function usageDataRows(list, labelFn, denominator) {
  return sortAiUsageRows(list, labelFn)
    .map((item) => {
      const label = labelFn(item);
      return `<tr>
        <td>${esc(label)}</td>
        ${usageCells(item, denominator)}
      </tr>`;
    })
    .join('');
}

function aiProjectExpansionKey(project) {
  return `${aiUsagePeriod}:${aiUsagePeriodKey}:${project}`;
}

function aiProjectLabel(project) {
  const value = project?.project_name || project?.project || project;
  return value === 'unknown' || !value ? 'Unattributed' : value;
}

function aiModelId(model) {
  return `${model?.provider || 'unknown'}::${model?.model || 'unknown'}`;
}

function aiModelExpansionKey(model) {
  return `${aiUsagePeriod}:${aiUsagePeriodKey}:${aiModelId(model)}`;
}

function restoreToggleFocus(selector, datasetKey, id) {
  [...document.querySelectorAll(selector)]
    .find((item) => item.dataset?.[datasetKey] === id)
    ?.focus();
}

function projectUsageRows(list, periodTotals) {
  const periodVolume = usageTokenVolume(periodTotals);
  return sortAiUsageRows(list, aiProjectLabel)
    .map((project) => {
      const projectName = aiProjectLabel(project);
      const projectId = project.project_id || 'unattributed';
      const expansionKey = aiProjectExpansionKey(projectId);
      const expanded = aiUsageExpandedProjects.has(expansionKey);
      const modelRows = expanded
        ? sortAiUsageRows(
            project.by_model || [],
            (model) => `${model.provider || 'unknown'} / ${model.model || 'unknown'}`,
          )
            .map(
              (model) => `<tr class="ai-project-model-row">
                <td><span class="ai-model-indent">↳</span>${esc(model.provider || 'unknown')} / ${esc(model.model || 'unknown')}</td>
                ${usageCells(model, usageTokenVolume(project))}
              </tr>`,
            )
            .join('')
        : '';
      return `<tr class="ai-project-row">
        <td><button type="button" class="ai-project-toggle" data-ai-project-toggle="${esc(projectId)}" data-project-id="${esc(projectId)}" aria-expanded="${expanded}" aria-label="${expanded ? 'Collapse' : 'Expand'} ${esc(projectName)}">${expanded ? '▾' : '▸'}</button>${esc(projectName)}</td>
        ${usageCells(project, periodVolume)}
      </tr>${modelRows}`;
    })
    .join('');
}

function modelUsageRows(list, periodTotals, forceExpanded = false) {
  const periodVolume = usageTokenVolume(periodTotals);
  return sortAiUsageRows(list, aiModelId)
    .map((model) => {
      const modelId = aiModelId(model);
      const expansionKey = aiModelExpansionKey(model);
      const expanded = forceExpanded || aiUsageExpandedModels.has(expansionKey);
      const projectRows = expanded
        ? sortAiUsageRows(model.by_project || [], aiProjectLabel)
            .map(
              (
                project,
              ) => `<tr class="ai-model-project-row" data-project-id="${esc(project.project_id || 'unattributed')}">
                <td><span class="ai-model-indent">↳</span>${esc(aiProjectLabel(project))}</td>
                ${usageCells(project, usageTokenVolume(model))}
              </tr>`,
            )
            .join('')
        : '';
      return `<tr class="ai-model-row">
        <td><button type="button" class="ai-project-toggle" data-ai-model-toggle="${esc(modelId)}" aria-expanded="${expanded}" aria-label="${expanded ? 'Collapse' : 'Expand'} ${esc(modelId)}">${expanded ? '▾' : '▸'}</button>${esc(model.provider || 'unknown')} / ${esc(model.model || 'unknown')}</td>
        ${usageCells(model, periodVolume)}
      </tr>${projectRows}`;
    })
    .join('');
}

function attributionQualityHtml(quality, workspaces) {
  const counts = quality?.attribution || {};
  const warnings = quality?.identity_warnings || [];
  const warningRows = warnings
    .map(
      (warning) =>
        `<li><span class="mono">${esc(warning.workspace_id || 'Unattributed')}</span> · ${esc(warning.code || 'identity_warning')}</li>`,
    )
    .join('');
  const configuredWorkspaces = (workspaces || []).filter(
    (workspace) => workspace.identity_confidence === 'configured',
  );
  const configuredRows = configuredWorkspaces
    .map(
      (workspace) =>
        `<li><span class="mono">${esc(workspace.workspace_id)}</span> · configured</li>`,
    )
    .join('');
  return `<details class="ai-attribution-quality">
    <summary>Attribution quality · ${esc(counts.git || 0)} Git · ${esc(counts.configured || 0)} configured · ${esc(counts.unattributed || 0)} unattributed</summary>
    <ul>${configuredRows}${warningRows || '<li>No identity warnings.</li>'}</ul>
  </details>`;
}

function paintAiTokensPanel() {
  const sourceOptions = [
    ['', 'All usage-capable sources'],
    ['antigravity.app', 'Antigravity app'],
    ['antigravity.ide', 'Antigravity IDE'],
    ['antigravity.cli', 'Antigravity CLI'],
    ['claude.code', 'Claude Code'],
    ['codex.home', 'Codex'],
  ]
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}" ${aiUsageFilter.source === value ? 'selected' : ''}>${esc(label)}</option>`,
    )
    .join('');
  const groupOptions = [
    ['model', 'By model'],
    ['project', 'By project'],
    ['period', 'By period'],
    ['surface', 'By surface'],
    ['tool', 'By tool'],
  ]
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}" ${aiUsageGroup === value ? 'selected' : ''}>${esc(label)}</option>`,
    )
    .join('');

  const periodTabs = ['day', 'week', 'month', 'year']
    .map(
      (period) =>
        `<button type="button" data-ai-usage-period="${period}" class="${aiUsagePeriod === period ? 'selected' : ''}">${period[0].toUpperCase()}${period.slice(1)}</button>`,
    )
    .join('');

  const series = aiPeriodSeries();
  const selectedKey = aiUsage ? ensureAiPeriodKey() : null;
  const selectedIndex = series.findIndex((row) => row.period === selectedKey);
  const selectedPeriod = selectedIndex >= 0 ? series[selectedIndex] : null;
  const canPrev = selectedIndex >= 0 && selectedIndex < series.length - 1;
  const canNext = selectedIndex > 0;

  const scopeTotals =
    (aiUsageGroup === 'model' || aiUsageGroup === 'project') && selectedPeriod
      ? selectedPeriod
      : aiUsage?.totals;
  const meta = aiUsage
    ? `${formatTokens(scopeTotals.input_tokens)} in · ${formatTokens(scopeTotals.cached_tokens)} cached · ${formatTokens(scopeTotals.output_tokens)} out · ${formatTokens(scopeTotals.cache_write_tokens)} cache write · ${esc(formatAiWhen(aiUsage.aggregatedAt))}`
    : 'Usage not loaded yet.';
  const usageScope = aiUsage?.scope;
  const scopeNote = usageScope
    ? `Selected GitHub repositories · ${esc(String(usageScope.repository_count ?? 0))} repositories · ${esc(String(usageScope.excluded_event_count ?? 0))} events excluded outside scope`
    : 'Selected GitHub repositories';
  const projectList = (selectedPeriod?.by_project || []).filter((item) => {
    const query = aiUsageProjectFilter.trim().toLowerCase();
    return !query || aiProjectLabel(item).toLowerCase().includes(query);
  });
  const modelList = (selectedPeriod?.by_model || []).filter((item) => {
    const query = aiUsageModelFilter.trim().toLowerCase();
    return !query || aiModelId(item).toLowerCase().includes(query);
  });
  const allProjectsExpanded =
    projectList.length > 0 &&
    projectList.every((item) =>
      aiUsageExpandedProjects.has(aiProjectExpansionKey(item.project_id || 'unattributed')),
    );
  const allModelsExpanded =
    modelList.length > 0 &&
    modelList.every((item) => aiUsageExpandedModels.has(aiModelExpansionKey(item)));

  let rows = '';
  let totalRow = '';
  let firstCol = 'Model';
  if (!paired) {
    rows = `<tr><td colspan="8"><div class="callout">Connect the companion to aggregate token usage.</div></td></tr>`;
  } else if (!aiUsage) {
    rows = `<tr><td colspan="8"><div class="callout">${
      aiUsageInvalidatedByIdentity
        ? 'Project grouping changed. Run Aggregate to rebuild project and model attribution.'
        : 'Click Aggregate to sum Antigravity, Claude Code, and Codex usage from local stores.'
    }</div></td></tr>`;
  } else if (aiUsageGroup === 'period') {
    firstCol = aiUsagePeriod[0].toUpperCase() + aiUsagePeriod.slice(1);
    if (!series.length) {
      rows = `<tr><td colspan="8"><div class="callout">No dated usage events found. Select a repository with a github.com remote.</div></td></tr>`;
    } else {
      rows = usageDataRows(
        series,
        (item) => formatAiPeriodLabel(item.period, aiUsagePeriod),
        usageTokenVolume(aiUsage.totals),
      );
      totalRow = usageTotalRow(aiUsage.totals);
    }
  } else if (aiUsageGroup === 'model') {
    firstCol = 'Model';
    if (!selectedPeriod) {
      rows = `<tr><td colspan="8"><div class="callout">No dated usage events found. Select a repository with a github.com remote.</div></td></tr>`;
    } else if (!modelList.length) {
      rows = `<tr><td colspan="8"><div class="callout">No matching model usage in ${esc(formatAiPeriodLabel(selectedKey))}.</div></td></tr>`;
    } else {
      rows = modelUsageRows(modelList, selectedPeriod);
      totalRow = usageTotalRow(selectedPeriod);
    }
  } else if (aiUsageGroup === 'project') {
    firstCol = 'Project';
    if (!selectedPeriod) {
      rows = `<tr><td colspan="8"><div class="callout">No dated usage events found. Select a repository with a github.com remote.</div></td></tr>`;
    } else if (!projectList.length) {
      rows = `<tr><td colspan="8"><div class="callout">No matching project usage in ${esc(formatAiPeriodLabel(selectedKey))}.</div></td></tr>`;
    } else {
      rows = projectUsageRows(projectList, selectedPeriod);
      totalRow = usageTotalRow(selectedPeriod);
    }
  } else if (aiUsageGroup === 'surface') {
    firstCol = 'Surface';
    const list = aiUsage.by_surface || [];
    if (!list.length) {
      rows = `<tr><td colspan="8"><div class="callout">No usage events found for these sources. Select a repository with a github.com remote.</div></td></tr>`;
    } else {
      rows = usageDataRows(list, (item) => item.label || '—', usageTokenVolume(aiUsage.totals));
      totalRow = usageTotalRow(aiUsage.totals);
    }
  } else {
    firstCol = 'Tool';
    const list = aiUsage.by_tool || [];
    if (!list.length) {
      rows = `<tr><td colspan="8"><div class="callout">No usage events found for these sources. Select a repository with a github.com remote.</div></td></tr>`;
    } else {
      rows = usageDataRows(list, (item) => item.tool || '—', usageTokenVolume(aiUsage.totals));
      totalRow = usageTotalRow(aiUsage.totals);
    }
  }

  const periodNav =
    aiUsage && (aiUsageGroup === 'model' || aiUsageGroup === 'project' || aiUsageGroup === 'period')
      ? `<div class="ai-period-nav">
          <div class="period-tabs">${periodTabs}</div>
          ${
            aiUsageGroup === 'model' || aiUsageGroup === 'project'
              ? `<div class="ai-period-shift">
                  <button type="button" class="btn" id="aiUsagePrev" ${!canPrev || aiBusy ? 'disabled' : ''}>←</button>
                  <strong>${esc(formatAiPeriodLabel(selectedKey))}</strong>
                  <button type="button" class="btn" id="aiUsageNext" ${!canNext || aiBusy ? 'disabled' : ''}>→</button>
                  <span class="small">${selectedIndex >= 0 ? `${selectedIndex + 1} / ${series.length}` : ''}</span>
                </div>`
              : `<p class="small" style="margin: 0">Each row is one ${esc(aiUsagePeriod)}.</p>`
          }
        </div>`
      : aiUsage
        ? `<div class="ai-period-nav"><p class="small" style="margin: 0">Surface and tool views are all-time. Switch Group to Model, Project, or Period for day/week/month/year.</p></div>`
        : '';

  const caveats = (aiUsage?.caveats || []).map((c) => `<li>${esc(c)}</li>`).join('');
  const projectControls =
    aiUsageGroup === 'project'
      ? `<label class="small" for="aiUsageProjectFilter">Project
          <input id="aiUsageProjectFilter" value="${esc(aiUsageProjectFilter)}" placeholder="Filter projects" />
        </label>
        <button type="button" class="btn" id="aiUsageExpandProjects" ${!projectList.length ? 'disabled' : ''}>${allProjectsExpanded ? 'Collapse all' : 'Expand all'}</button>`
      : '';
  const modelControls =
    aiUsageGroup === 'model'
      ? `<label class="small" for="aiUsageModelFilter">Model
          <input id="aiUsageModelFilter" value="${esc(aiUsageModelFilter)}" placeholder="Filter models" />
        </label>
        <button type="button" class="btn" id="aiUsageExpandModels" ${!modelList.length ? 'disabled' : ''}>${allModelsExpanded ? 'Collapse all' : 'Expand all'}</button>`
      : '';
  const warningCount = Number(aiUsage?.quality?.warning_count || 0);
  const reconciliation = aiUsage?.quality?.reconciliation || {};
  const reconciliationDifferences = Number(reconciliation.different || 0);
  const unknownModels = (aiUsage?.by_model || []).filter(
    (item) => !item.model || item.model === 'unknown',
  ).length;
  const cache = aiUsage?.cache;
  const qualityNotes = aiUsage
    ? `<div class="callout" style="margin-top: 18px"><strong>Accounting quality</strong><div class="small" style="margin-top: 8px">
        ${esc(String(aiUsage.conversation_count ?? '—'))} conversations ·
        ${esc(String(aiUsage.execution_count ?? '—'))} executions ·
        ${esc(String(aiUsage.event_count ?? aiUsage.totals?.events ?? 0))} unique provider/model calls ·
        ${esc(String(warningCount))} parser warnings ·
        ${esc(String(unknownModels))} unknown models ·
        ${esc(String(reconciliationDifferences))} reconciliation differences ·
        cache ${esc(cache?.status || 'unavailable')}${cache ? ` (${esc(String(cache.hits || 0))} hits, ${esc(String((cache.misses || 0) + (cache.reparsed || 0)))} parsed)` : ''}
      </div>${cache?.status === 'bypassed' ? '<div class="small" style="margin-top: 6px">Cache was bypassed; totals came from a direct read-only scan.</div>' : ''}</div>`
    : '';
  const attributionQuality = aiUsage
    ? attributionQualityHtml(aiUsage.quality, aiUsage.by_workspace || [])
    : '';

  return /* HTML */ `
    <div class="ai-filters">
      <label class="small" for="aiUsageSourceFilter">Source</label>
      <select id="aiUsageSourceFilter">
        ${sourceOptions}
      </select>
      <label class="small" for="aiUsageGroup">Group</label>
      <select id="aiUsageGroup">
        ${groupOptions}
      </select>
      ${projectControls} ${modelControls}
      <button class="btn primary" id="aiUsageBtn" ${!paired || aiBusy ? 'disabled' : ''}>
        ${aiBusy && aiTab === 'tokens' ? 'Aggregating…' : '<span class="arrow">⟳</span> Aggregate'}
      </button>
    </div>
    ${periodNav}
    <div class="callout" style="margin: 0 0 18px"><strong>${scopeNote}</strong></div>
    <div class="machine-meta" style="margin: 0 0 18px">${meta}</div>
    <div class="ai-table-scroll">
      <table class="ai-table">
        <thead>
          <tr>
            ${aiUsageHeaderCell(firstCol, 'label')} ${aiUsageHeaderCell('Input', 'input_tokens')}
            ${aiUsageHeaderCell('Cached', 'cached_tokens')}
            ${aiUsageHeaderCell('Output', 'output_tokens')}
            ${aiUsageHeaderCell('Cache write', 'cache_write_tokens')}
            ${aiUsageHeaderCell('Total', 'total')} ${aiUsageHeaderCell('Share', 'share')}
            ${aiUsageHeaderCell('Unique calls', 'events')}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
        ${totalRow ? `<tfoot>${totalRow}</tfoot>` : ''}
      </table>
    </div>
    ${qualityNotes} ${attributionQuality}
    ${caveats
      ? `<div class="callout" style="margin-top: 18px"><strong>Caveats</strong><ul style="margin: 8px 0 0; padding-left: 18px">${caveats}</ul></div>`
      : ''}
  `;
}

function paintAiSearchPanel() {
  const sourceOptions = [
    ['', 'All wave-1 sources'],
    ['antigravity.app', 'Antigravity app'],
    ['antigravity.ide', 'Antigravity IDE'],
    ['antigravity.cli', 'Antigravity CLI'],
    ['claude.code', 'Claude Code'],
    ['codex.home', 'Codex'],
    ['cursor.cli', 'Cursor CLI'],
    ['cursor.agent-transcripts', 'Cursor agent transcripts'],
  ]
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}" ${aiSearchFilter.source === value ? 'selected' : ''}>${esc(label)}</option>`,
    )
    .join('');

  const hits = aiSearch?.hits || [];
  let rows = '';
  if (!paired) {
    rows = `<tr><td colspan="4"><div class="callout">Connect the companion to search local transcripts.</div></td></tr>`;
  } else if (!aiSearch) {
    rows = `<tr><td colspan="4"><div class="callout">Enter a query and click Search. Scans Antigravity, Claude, Codex, and Cursor stores on this machine.</div></td></tr>`;
  } else if (!hits.length) {
    rows = `<tr><td colspan="4"><div class="callout">No hits for “${esc(aiSearch.q)}”. Select a repository with a github.com remote.</div></td></tr>`;
  } else {
    hits.forEach((hit) => {
      rows += `<tr>
        <td>${esc(hit.tool)}<div class="small">${esc(hit.surface)}</div></td>
        <td class="small">${esc(hit.session_id || '—')}${hit.line ? `<div>L${esc(String(hit.line))}</div>` : ''}</td>
        <td>${esc(hit.snippet || '')}</td>
        <td class="ai-path small">${esc(hit.path || '')}</td>
      </tr>`;
    });
  }

  const meta = aiSearch
    ? `Selected GitHub repositories · ${aiSearch.count} hit${aiSearch.count === 1 ? '' : 's'}${aiSearch.truncated ? ' (truncated)' : ''} · ${esc(aiSearch.engine || 'python')} · ${esc(formatAiWhen(aiSearch.searchedAt))}`
    : 'Selected GitHub repositories · Search not run yet.';

  return /* HTML */ `
    <div class="ai-filters">
      <label class="small" for="aiSearchQuery">Query</label>
      <input
        id="aiSearchQuery"
        type="text"
        value="${esc(aiSearchFilter.q)}"
        placeholder="login bug"
        style="min-width: 180px"
      />
      <label class="small" for="aiSearchSourceFilter">Source</label>
      <select id="aiSearchSourceFilter">
        ${sourceOptions}
      </select>
      <button class="btn primary" id="aiSearchBtn" ${!paired || aiBusy ? 'disabled' : ''}>
        ${aiBusy && aiTab === 'search' ? 'Searching…' : '<span class="arrow">⟳</span> Search'}
      </button>
    </div>
    <div class="machine-meta" style="margin: 0 0 18px">${meta}</div>
    <table class="ai-table">
      <thead>
        <tr>
          <th>Tool</th>
          <th>Session</th>
          <th>Snippet</th>
          <th>Path</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="small" style="margin-top: 12px">
      Literal, case-insensitive match. Prefers ripgrep when installed; otherwise Python line scan.
      Treat hits as sensitive local logs.
    </p>
  `;
}

function paintAi() {
  const root = $('aiView');
  if (!root) return;
  const action =
    aiTab === 'detect'
      ? `<button class="btn primary" id="aiDetectBtn" ${!paired || aiBusy ? 'disabled' : ''}>
          ${aiBusy && aiTab === 'detect' ? 'Detecting…' : '<span class="arrow">⟳</span> Detect'}
        </button>`
      : '';
  const panel =
    aiTab === 'detect'
      ? paintAiDetectPanel()
      : aiTab === 'sessions'
        ? paintAiSessionsPanel()
        : aiTab === 'tokens'
          ? paintAiTokensPanel()
          : paintAiSearchPanel();
  root.innerHTML = /* HTML */ `
    <div class="heading">
      <div>
        <h1>AI</h1>
        <p>Local discussion stores on this machine. Nothing is uploaded.</p>
      </div>
      <div class="actions">${action}</div>
    </div>
    <div class="ai-tabs">
      <button
        type="button"
        class="ai-tab ${aiTab === 'detect' ? 'active' : ''}"
        data-ai-tab="detect"
      >
        Detect
      </button>
      <button
        type="button"
        class="ai-tab ${aiTab === 'sessions' ? 'active' : ''}"
        data-ai-tab="sessions"
      >
        Conversations
      </button>
      <button
        type="button"
        class="ai-tab ${aiTab === 'tokens' ? 'active' : ''}"
        data-ai-tab="tokens"
      >
        Tokens
      </button>
      <button
        type="button"
        class="ai-tab ${aiTab === 'search' ? 'active' : ''}"
        data-ai-tab="search"
      >
        Search
      </button>
    </div>
    ${panel}
    <p class="small" style="margin-top: 16px">
      Detects local store availability. Conversations, token calls, and search are limited to
      Selected GitHub repositories.
    </p>
  `;
  document.querySelectorAll('[data-ai-tab]').forEach((button) => {
    button.onclick = () => {
      aiTab = button.dataset.aiTab;
      paintAi();
    };
  });
  if ($('aiDetectBtn')) $('aiDetectBtn').onclick = runAiDetect;
  if ($('aiSessionsBtn')) $('aiSessionsBtn').onclick = runAiSessions;
  if ($('aiUsageBtn')) $('aiUsageBtn').onclick = runAiUsage;
  if ($('aiSearchBtn')) $('aiSearchBtn').onclick = runAiSearch;
  if ($('aiSourceFilter')) {
    $('aiSourceFilter').onchange = (event) => {
      aiSessionFilter.source = event.target.value;
    };
  }
  if ($('aiProjectFilter')) {
    $('aiProjectFilter').oninput = (event) => {
      aiSessionFilter.project = event.target.value;
    };
  }
  if ($('aiUsageSourceFilter')) {
    $('aiUsageSourceFilter').onchange = (event) => {
      aiUsageFilter.source = event.target.value;
    };
  }
  if ($('aiUsageGroup')) {
    $('aiUsageGroup').onchange = (event) => {
      aiUsageGroup = event.target.value;
      paintAi();
      $('aiUsageGroup')?.focus();
    };
  }
  if ($('aiUsageProjectFilter')) {
    $('aiUsageProjectFilter').oninput = (event) => {
      aiUsageProjectFilter = event.target.value;
      paintAi();
      const input = $('aiUsageProjectFilter');
      input?.focus();
      input?.setSelectionRange(input.value.length, input.value.length);
    };
  }
  if ($('aiUsageModelFilter')) {
    $('aiUsageModelFilter').oninput = (event) => {
      aiUsageModelFilter = event.target.value;
      paintAi();
      const input = $('aiUsageModelFilter');
      input?.focus();
      input?.setSelectionRange(input.value.length, input.value.length);
    };
  }
  document.querySelectorAll('[data-ai-usage-sort]').forEach((button) => {
    button.onclick = () => {
      const key = button.dataset.aiUsageSort;
      setAiUsageSort(key);
      paintAi();
      restoreToggleFocus('[data-ai-usage-sort]', 'aiUsageSort', key);
    };
  });
  document.querySelectorAll('[data-ai-project-toggle]').forEach((button) => {
    button.onclick = () => {
      const id = button.dataset.aiProjectToggle || 'unknown';
      const key = aiProjectExpansionKey(id);
      if (aiUsageExpandedProjects.has(key)) aiUsageExpandedProjects.delete(key);
      else aiUsageExpandedProjects.add(key);
      paintAi();
      restoreToggleFocus('[data-ai-project-toggle]', 'aiProjectToggle', id);
    };
  });
  if ($('aiUsageExpandProjects')) {
    $('aiUsageExpandProjects').onclick = () => {
      const selected = aiPeriodSeries().find((row) => row.period === aiUsagePeriodKey);
      const query = aiUsageProjectFilter.trim().toLowerCase();
      const visible = (selected?.by_project || []).filter(
        (item) => !query || aiProjectLabel(item).toLowerCase().includes(query),
      );
      const keys = visible.map((item) => aiProjectExpansionKey(item.project_id || 'unattributed'));
      const expand = keys.some((key) => !aiUsageExpandedProjects.has(key));
      keys.forEach((key) => {
        if (expand) aiUsageExpandedProjects.add(key);
        else aiUsageExpandedProjects.delete(key);
      });
      paintAi();
      $('aiUsageExpandProjects')?.focus();
    };
  }
  document.querySelectorAll('[data-ai-model-toggle]').forEach((button) => {
    button.onclick = () => {
      const id = button.dataset.aiModelToggle;
      const key = `${aiUsagePeriod}:${aiUsagePeriodKey}:${id}`;
      if (aiUsageExpandedModels.has(key)) aiUsageExpandedModels.delete(key);
      else aiUsageExpandedModels.add(key);
      paintAi();
      restoreToggleFocus('[data-ai-model-toggle]', 'aiModelToggle', id);
    };
  });
  if ($('aiUsageExpandModels')) {
    $('aiUsageExpandModels').onclick = () => {
      const selected = aiPeriodSeries().find((row) => row.period === aiUsagePeriodKey);
      const query = aiUsageModelFilter.trim().toLowerCase();
      const visible = (selected?.by_model || []).filter(
        (item) => !query || aiModelId(item).toLowerCase().includes(query),
      );
      const keys = visible.map((item) => aiModelExpansionKey(item));
      const expand = keys.some((key) => !aiUsageExpandedModels.has(key));
      keys.forEach((key) => {
        if (expand) aiUsageExpandedModels.add(key);
        else aiUsageExpandedModels.delete(key);
      });
      paintAi();
      $('aiUsageExpandModels')?.focus();
    };
  }
  document.querySelectorAll('[data-ai-usage-period]').forEach((button) => {
    button.onclick = () => {
      const period = button.dataset.aiUsagePeriod;
      aiUsagePeriod = period;
      aiUsagePeriodKey = null;
      paintAi();
      restoreToggleFocus('[data-ai-usage-period]', 'aiUsagePeriod', period);
    };
  });
  if ($('aiUsagePrev')) {
    $('aiUsagePrev').onclick = () => {
      const series = aiPeriodSeries();
      const index = series.findIndex((row) => row.period === aiUsagePeriodKey);
      if (index >= 0 && index < series.length - 1) {
        aiUsagePeriodKey = series[index + 1].period;
        paintAi();
        $('aiUsagePrev')?.focus();
      }
    };
  }
  if ($('aiUsageNext')) {
    $('aiUsageNext').onclick = () => {
      const series = aiPeriodSeries();
      const index = series.findIndex((row) => row.period === aiUsagePeriodKey);
      if (index > 0) {
        aiUsagePeriodKey = series[index - 1].period;
        paintAi();
        $('aiUsageNext')?.focus();
      }
    };
  }
  if ($('aiSearchQuery')) {
    $('aiSearchQuery').oninput = (event) => {
      aiSearchFilter.q = event.target.value;
    };
    $('aiSearchQuery').onkeydown = (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        runAiSearch();
      }
    };
  }
  if ($('aiSearchSourceFilter')) {
    $('aiSearchSourceFilter').onchange = (event) => {
      aiSearchFilter.source = event.target.value;
    };
  }
}
function dateKey(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
function mondayOf(date) {
  const value = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const day = (value.getDay() + 6) % 7;
  value.setDate(value.getDate() - day);
  return value;
}
function isoWeekKey(date) {
  const utc = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = utc.getUTCDay() || 7;
  utc.setUTCDate(utc.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((utc - yearStart) / 86400000 + 1) / 7);
  return `${utc.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}
function monthKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}
function commitsOnDay(map, date) {
  return map[dateKey(date)] || 0;
}
function sumRange(map, start, days) {
  let total = 0;
  for (let i = 0; i < days; i += 1) {
    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    total += commitsOnDay(map, day);
  }
  return total;
}
function defaultCursor(period, stats) {
  const now = new Date();
  if (period === 'day') return new Date(now.getFullYear(), now.getMonth(), 1);
  if (period === 'week') return mondayOf(now);
  if (period === 'month') return new Date(now.getFullYear(), 0, 1);
  const years = Object.keys(stats?.commitsByDate || {})
    .map((key) => Number(key.slice(0, 4)))
    .filter((year) => !Number.isNaN(year));
  const latest = years.length ? Math.max(...years) : now.getFullYear();
  return new Date(latest - (latest % 12), 0, 1);
}
function ensureCursor(stats) {
  if (!detailCursor) detailCursor = defaultCursor(detailPeriod, stats);
  return detailCursor;
}
function ensureStatsCursor(stats) {
  if (!statsCursor) statsCursor = defaultCursor(statsPeriod, stats);
  return statsCursor;
}
function shiftCursor(delta) {
  const cursor = detailCursor || new Date();
  if (detailPeriod === 'day') {
    detailCursor = new Date(cursor.getFullYear(), cursor.getMonth() + delta, 1);
  } else if (detailPeriod === 'week') {
    detailCursor = new Date(
      cursor.getFullYear(),
      cursor.getMonth(),
      cursor.getDate() + delta * 7 * 12,
    );
  } else if (detailPeriod === 'month') {
    detailCursor = new Date(cursor.getFullYear() + delta, 0, 1);
  } else {
    detailCursor = new Date(cursor.getFullYear() + delta * 12, 0, 1);
  }
}
function shiftStatsCursor(delta) {
  const cursor = statsCursor || new Date();
  if (statsPeriod === 'day') {
    statsCursor = new Date(cursor.getFullYear(), cursor.getMonth() + delta, 1);
  } else if (statsPeriod === 'week') {
    statsCursor = new Date(
      cursor.getFullYear(),
      cursor.getMonth(),
      cursor.getDate() + delta * 7 * 12,
    );
  } else {
    statsCursor = new Date(cursor.getFullYear() + delta, 0, 1);
  }
}
function weekCode(start) {
  return isoWeekKey(start).replace(/^(\d{4})-W(\d{2})$/, (_, y, w) => `W${w}/${y.slice(-2)}`);
}
function shortDay(date) {
  const dd = String(date.getDate()).padStart(2, '0');
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  return `${dd}/${mm}`;
}
function weekRangeDates(start, { withYear = true } = {}) {
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
  if (!withYear) return `${shortDay(start)}-${shortDay(end)}`;
  return `${formatDate(dateKey(start) + 'T12:00:00')} – ${formatDate(dateKey(end) + 'T12:00:00')}`;
}
function weekLabel(start, { withYear = false } = {}) {
  return `${weekCode(start)} (${weekRangeDates(start, { withYear })})`;
}
function periodLabel(period, start, mode = detailPeriod) {
  if (start && (mode === 'week' || /W\d{2}/.test(String(period)))) {
    return weekLabel(start);
  }
  if (mode === 'day' || /^\d{4}-\d{2}-\d{2}$/.test(period)) {
    return formatDate(String(period) + 'T12:00:00');
  }
  if (mode === 'week' || /W\d{2}$/.test(period)) {
    return String(period).replace(/^(\d{4})-W(\d{2})$/, (_, y, w) => `W${w}/${y.slice(-2)}`);
  }
  if (mode === 'month' || /^\d{4}-\d{2}$/.test(period)) {
    const [y, m] = String(period).split('-');
    return y && m ? `${m}/${y.slice(-2)}` : period;
  }
  return String(period);
}
function shouldLabel(index, total, mode = detailPeriod) {
  if (total <= 14) return true;
  if (mode === 'day') return index % 5 === 0 || index === total - 1;
  if (mode === 'month') return index % 2 === 0 || index === total - 1;
  return true;
}
function axisLabel(item, index, total, mode = detailPeriod) {
  if (!shouldLabel(index, total, mode)) return '';
  if (mode === 'week' && item.start) {
    return `<i>${esc(weekLabel(item.start))}</i>`;
  }
  return `<i>${esc(periodLabel(item.period, item.start, mode))}</i>`;
}
function seriesForView(map, mode = detailPeriod, cursor) {
  const active =
    cursor ||
    (mode === detailPeriod
      ? ensureCursor({ commitsByDate: map })
      : ensureStatsCursor({ commitsByDate: map }));
  if (mode === 'day') {
    const year = active.getFullYear();
    const month = active.getMonth();
    const days = new Date(year, month + 1, 0).getDate();
    return Array.from({ length: days }, (_, i) => {
      const date = new Date(year, month, i + 1);
      return { period: dateKey(date), commits: commitsOnDay(map, date) };
    });
  }
  if (mode === 'week') {
    const end = mondayOf(active);
    return Array.from({ length: 12 }, (_, i) => {
      const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - (11 - i) * 7);
      return { period: isoWeekKey(start), commits: sumRange(map, start, 7), start };
    });
  }
  if (mode === 'month') {
    const year = active.getFullYear();
    return Array.from({ length: 12 }, (_, i) => {
      const start = new Date(year, i, 1);
      const days = new Date(year, i + 1, 0).getDate();
      return { period: monthKey(start), commits: sumRange(map, start, days) };
    });
  }
  const startYear = active.getFullYear();
  return Array.from({ length: 12 }, (_, i) => {
    const year = startYear + i;
    let commits = 0;
    Object.entries(map).forEach(([key, count]) => {
      if (key.startsWith(String(year))) commits += count;
    });
    return { period: String(year), commits };
  });
}
function calendarTitle(cursor, mode = detailPeriod) {
  if (mode === 'day') {
    return cursor.toLocaleString(undefined, { month: 'long', year: 'numeric' });
  }
  if (mode === 'week') {
    const end = mondayOf(cursor);
    const start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 11 * 7);
    return `${weekLabel(start, { withYear: true })} → ${weekLabel(end, { withYear: true })}`;
  }
  if (mode === 'month') return String(cursor.getFullYear());
  return `${cursor.getFullYear()} – ${cursor.getFullYear() + 11}`;
}
function calendarGrid(map, series, linesMap = null) {
  const peak = Math.max(0, ...series.map((item) => item.commits || 0));
  if (detailPeriod === 'day') {
    const cursor = ensureCursor({ commitsByDate: map });
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const first = new Date(year, month, 1);
    const startPad = (first.getDay() + 6) % 7;
    const days = new Date(year, month + 1, 0).getDate();
    const heads = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
      .map((day) => `<div class="cal-dow">${day}</div>`)
      .join('');
    const cells = [];
    for (let i = 0; i < startPad; i += 1) cells.push('<div class="cal-cell empty"></div>');
    for (let day = 1; day <= days; day += 1) {
      const date = new Date(year, month, day);
      const commits = commitsOnDay(map, date);
      const lines = linesMap ? formatLines(linesMap[dateKey(date)]) : '';
      const tip = `${formatDate(dateKey(date) + 'T12:00:00')}: ${commits} commit${
        commits === 1 ? '' : 's'
      }${lines && lines !== '—' ? ` · ${lines}` : ''}`;
      cells.push(/* HTML */ `
        <div
          class="cal-cell ${commits ? 'active' : 'zero'} ${commits && commits === peak
            ? 'peak'
            : ''}"
          title="${esc(tip)}"
        >
          <span class="d">${esc(day)}</span>
          <span class="n">${esc(commits)}</span>
        </div>
      `);
    }
    return /* HTML */ `
      <div class="cal-dow-row">${heads}</div>
      <div class="cal-grid days">${cells.join('')}</div>
    `;
  }
  const cls = detailPeriod === 'week' ? 'weeks' : detailPeriod === 'month' ? 'months' : 'years';
  return /* HTML */ `
    <div class="cal-grid ${cls}">
      ${series
        .map((item) => {
          const commits = item.commits || 0;
          const label =
            detailPeriod === 'week' && item.start
              ? weekLabel(item.start)
              : periodLabel(item.period, item.start);
          const lines = linesMap ? formatLines(linesForBucket(linesMap, item, detailPeriod)) : '';
          const tip = `${label}: ${commits} commit${commits === 1 ? '' : 's'}${
            lines && lines !== '—' ? ` · ${lines}` : ''
          }`;
          return /* HTML */ `
            <div
              class="cal-cell ${commits ? 'active' : 'zero'} ${commits && commits === peak
                ? 'peak'
                : ''}"
              title="${esc(tip)}"
            >
              <span class="d">${esc(label)}</span>
              <span class="n">${esc(commits)}</span>
            </div>
          `;
        })
        .join('')}
    </div>
  `;
}
function timelineChart(map, series, mode, extraMeta = '', linesMap = null) {
  if (!series.length) {
    return '<p class="note">No commit timeline in cache yet. Load or refresh portfolio stats.</p>';
  }
  const counts = series.map((item) => item.commits || 0);
  const max = Math.max(1, ...counts);
  const total = counts.reduce((sum, n) => sum + n, 0);
  const active = counts.filter((n) => n > 0).length;
  const peak = Math.max(...counts);
  const mid = Math.round(max / 2);
  const viewLines = linesMap
    ? series.reduce(
        (sum, item) => addLines(sum, linesForBucket(linesMap, item, mode)),
        emptyLines(),
      )
    : null;
  const bars = series
    .map((item, index) => {
      const commits = item.commits || 0;
      const height = commits ? Math.max(8, Math.round((commits / max) * 170)) : 2;
      const label = axisLabel(item, index, series.length, mode);
      const lines = linesMap ? formatLines(linesForBucket(linesMap, item, mode)) : '';
      const tip = `${periodLabel(item.period, item.start, mode)} · ${commits} commit${
        commits === 1 ? '' : 's'
      }${lines && lines !== '—' ? ` · ${lines}` : ''}`;
      return /* HTML */ `
        <div
          class="timeline-bar ${commits ? '' : 'zero'} ${commits === peak && peak ? 'peak' : ''}"
          title="${esc(tip)}"
        >
          ${commits ? `<em>${esc(commits)}</em>` : ''}
          <b style="height:${height}px"></b>
          ${label ? `<span>${label}</span>` : ''}
        </div>
      `;
    })
    .join('');
  const linesMeta =
    viewLines && (viewLines.additions || viewLines.deletions)
      ? `<span>Lines <strong>${esc(formatLines(viewLines))}</strong></span>`
      : '';
  return /* HTML */ `
    <div class="timeline-panel">
      <div class="timeline-meta">
        <span>
          In view
          <strong>${esc(total)}</strong>
        </span>
        <span>
          Active
          <strong>${esc(active)}/${esc(series.length)}</strong>
        </span>
        <span>
          Peak
          <strong>${esc(peak)}</strong>
        </span>
        ${linesMeta} ${extraMeta}
      </div>
      <div class="timeline-chart" role="img" aria-label="Commit timeline">
        <div class="timeline-y">
          <span>${esc(max)}</span>
          <span>${esc(mid)}</span>
          <span>0</span>
        </div>
        <div class="timeline-plot count-${esc(series.length)}">${bars}</div>
      </div>
    </div>
  `;
}
function timelineViz(stats) {
  const map = stats?.commitsByDate || {};
  if (!Object.keys(map).length) {
    return '<p class="note">No commit timeline in cache yet. Refresh stats.</p>';
  }
  const cursor = ensureCursor(stats);
  const series = seriesForView(map, detailPeriod, cursor);
  const linesMap = stats?.linesByDate || null;
  return /* HTML */ `
    ${timelineChart(map, series, detailPeriod, '', linesMap)}
    <div class="cal-nav">
      <button class="btn" id="calPrev">← Prev</button>
      <h3>${esc(calendarTitle(cursor, detailPeriod))}</h3>
      <button class="btn" id="calNext">Next →</button>
    </div>
    ${calendarGrid(map, series, linesMap)}
  `;
}
function mergedCommitsByDate() {
  const map = {};
  const sources = statsProjects.length ? statsProjects : paths.map((path) => statsByPath[path]);
  sources.filter(Boolean).forEach((stats) => {
    const byDate = stats?.commitsByDate || {};
    Object.entries(byDate).forEach(([day, count]) => {
      map[day] = (map[day] || 0) + (count || 0);
    });
  });
  return map;
}
function commitsInBucket(map, item, mode) {
  if (mode === 'day') return map[item.period] || 0;
  if (mode === 'week' && item.start) return sumRange(map, item.start, 7);
  if (mode === 'month' && /^\d{4}-\d{2}$/.test(item.period)) {
    const [y, m] = item.period.split('-').map(Number);
    const start = new Date(y, m - 1, 1);
    return sumRange(map, start, new Date(y, m, 0).getDate());
  }
  return 0;
}
function topReposForSeries(series, mode, limit = 5) {
  const canonical = statsProjects.flatMap((project) => project.repositories || []);
  return (canonical.length ? canonical : paths.map((path) => statsByPath[path]).filter(Boolean))
    .map((s) => {
      const map = s?.commitsByDate || {};
      const commits = series.reduce((sum, item) => sum + commitsInBucket(map, item, mode), 0);
      return {
        path: s?.primary_workspace_id || s?.path,
        name: s?.repository_name || s?.name || 'Repository',
        commits,
      };
    })
    .filter((row) => row.commits > 0)
    .sort((a, b) => b.commits - a.commits || a.name.localeCompare(b.name))
    .slice(0, limit);
}

function compareStatsHierarchy(a, b) {
  const name = (item) =>
    String(item.project_name || item.repository_name || item.name || '').toLowerCase();
  if (boardSortKey === 'name') return name(a).localeCompare(name(b)) * boardSortDir;
  let av = a[boardSortKey];
  let bv = b[boardSortKey];
  if (boardSortKey === 'lastCommitAt') {
    av = av ? new Date(av).getTime() : 0;
    bv = bv ? new Date(bv).getTime() : 0;
  }
  av = av == null ? -1 : av;
  bv = bv == null ? -1 : bv;
  if (av === bv) return name(a).localeCompare(name(b));
  return av < bv ? -boardSortDir : boardSortDir;
}

function statsHierarchyRows(projects, forceExpanded = false) {
  const numberCell = (value) =>
    value == null ? '<td class="num muted">—</td>' : `<td class="num">${esc(value)}</td>`;
  return [...(projects || [])]
    .sort(compareStatsHierarchy)
    .map((project) => {
      const projectId = project.project_id || 'unattributed';
      const projectExpanded = forceExpanded || statsExpandedProjects.has(projectId);
      const projectRow = `<tr class="stats-project-row">
        <td><button type="button" class="stats-tree-toggle" data-stats-project-toggle="${esc(projectId)}" aria-expanded="${projectExpanded}">${projectExpanded ? '▾' : '▸'}</button><strong>${esc(project.project_name || project.name || 'Unattributed')}</strong></td>
        ${numberCell(project.commits7d)}${numberCell(project.commits30d)}${numberCell(project.commitCount)}
        <td>${esc(project.lastCommitAt ? formatDate(project.lastCommitAt) : '—')}</td>
        <td>${sparkline(project.commitsByDate || {})}</td>
      </tr>`;
      if (!projectExpanded) return projectRow;
      const repositoryRows = [...(project.repositories || [])]
        .sort(compareStatsHierarchy)
        .map((repository) => {
          const repositoryId = repository.repository_id || 'unattributed';
          const repositoryExpanded = forceExpanded || statsExpandedRepositories.has(repositoryId);
          const repositoryRow = `<tr class="stats-repository-row" data-repository-id="${esc(repositoryId)}">
            <td><span class="stats-tree-indent">↳</span><button type="button" class="stats-tree-toggle" data-stats-repository-toggle="${esc(repositoryId)}" aria-expanded="${repositoryExpanded}">${repositoryExpanded ? '▾' : '▸'}</button>${esc(repository.repository_name || repository.name || 'Repository')}</td>
            ${numberCell(repository.commits7d)}${numberCell(repository.commits30d)}${numberCell(repository.commitCount)}
            <td>${esc(repository.lastCommitAt ? formatDate(repository.lastCommitAt) : '—')}</td>
            <td>${sparkline(repository.commitsByDate || {})}</td>
          </tr>`;
          if (!repositoryExpanded) return repositoryRow;
          const workspaceRows = (repository.workspaces || [])
            .map((workspace) => {
              const dirty = Number(workspace.modified || 0) + Number(workspace.untracked || 0);
              const sync = workspace.error
                ? workspace.error
                : `${workspace.ahead ?? '?'} ahead · ${workspace.behind ?? '?'} behind · ${dirty} dirty`;
              return `<tr class="stats-workspace-row" data-workspace-id="${esc(workspace.workspace_id)}" data-path="${esc(workspace.path || workspace.workspace_id)}" tabindex="0">
                <td><span class="stats-tree-indent">↳ ↳</span>${workspace.primary ? '<span class="badge pull">Primary</span> ' : ''}${esc(workspace.workspace_name || workspace.name || 'Workspace')}<div class="repo-path">${esc(workspace.workspace_id)}</div></td>
                <td colspan="3"><span class="branch">⑂ ${esc(workspace.branch || 'Unknown')}</span></td>
                <td colspan="2" class="small">${esc(sync)} · local refs</td>
              </tr>`;
            })
            .join('');
          return repositoryRow + workspaceRows;
        })
        .join('');
      return projectRow + repositoryRows;
    })
    .join('');
}
function sparkline(map, days = 30) {
  const now = new Date();
  const values = Array.from({ length: days }, (_, i) => {
    const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (days - 1 - i));
    return commitsOnDay(map, date);
  });
  const peak = Math.max(1, ...values);
  const width = 72;
  const height = 22;
  const gap = 1;
  const bar = Math.max(1, (width - gap * (days - 1)) / days);
  const rects = values
    .map((value, i) => {
      const h = value ? Math.max(2, Math.round((value / peak) * (height - 2))) : 1;
      const x = i * (bar + gap);
      const y = height - h;
      return `<rect class="${value ? '' : 'zero'}" x="${x.toFixed(2)}" y="${y}" width="${bar.toFixed(2)}" height="${h}" rx="0.5"></rect>`;
    })
    .join('');
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" aria-hidden="true">${rects}</svg>`;
}
function boardRows() {
  return paths.map((path) => {
    const s = statsByPath[path];
    const name = s?.name || path.split(/[\\/]/).pop();
    return {
      path,
      name,
      commits7d: s?.commits7d ?? null,
      commits30d: s?.commits30d ?? null,
      commitCount: s?.commitCount ?? null,
      lastCommitAt: s?.lastCommitAt || null,
      map: s?.commitsByDate || {},
      hasCache: !!s,
    };
  });
}
function compareBoard(a, b) {
  const key = boardSortKey;
  let av = a[key];
  let bv = b[key];
  if (key === 'name') {
    av = a.name.toLowerCase();
    bv = b.name.toLowerCase();
    return av < bv ? -boardSortDir : av > bv ? boardSortDir : 0;
  }
  if (key === 'lastCommitAt') {
    av = av ? new Date(av).getTime() : 0;
    bv = bv ? new Date(bv).getTime() : 0;
  }
  av = av == null ? -1 : av;
  bv = bv == null ? -1 : bv;
  if (av === bv) return a.name.localeCompare(b.name);
  return av < bv ? -boardSortDir : boardSortDir;
}
function paintStats() {
  const pulse = portfolioNumbers();
  const map = mergedCommitsByDate();
  const cursor = ensureStatsCursor({ commitsByDate: map });
  const series = seriesForView(map, statsPeriod, cursor);
  const tops = topReposForSeries(series, statsPeriod);
  const hierarchyRows = statsHierarchyRows(statsProjects);
  const hasCache = pulse.cached > 0;
  const coverage = `<span>Coverage <strong>${esc(pulse.cached)}/${esc(pulse.tracked)}</strong></span>`;
  $('statsView').innerHTML = /* HTML */ `
    <div class="heading">
      <div>
        <h1>Stats across projects</h1>
        <p>Deduplicated repository history with each workspace’s local branch status.</p>
      </div>
      <div class="actions">
        <button
          class="btn primary"
          id="statsScan"
          ${!paired || busy || statsBusy || !paths.length ? 'disabled' : ''}
        >
          ${busy || statsBusy ? 'Scanning…' : '<span class="arrow">⟳</span> Scan'}
        </button>
      </div>
    </div>
    ${!paired
      ? '<div class="callout">Connect the companion to load local Git stats.</div>'
      : !paths.length
        ? '<div class="callout">Track repositories first, then Scan.</div>'
        : gitStatsInvalidatedByIdentity
          ? '<div class="callout">Project grouping changed. Click Scan to rebuild Git attribution and portfolio totals.</div>'
          : !hasCache
            ? '<div class="callout">No cached stats yet. Click Scan to refresh sync status and portfolio stats.</div>'
            : ''}
    <section class="stats-section">
      <div class="section-head">
        <h2>Activity</h2>
        <div class="period-tabs">
          ${['day', 'week', 'month']
            .map(
              (period) => /* HTML */ `
                <button
                  data-stats-period="${period}"
                  class="${statsPeriod === period ? 'selected' : ''}"
                >
                  ${period[0].toUpperCase() + period.slice(1)}
                </button>
              `,
            )
            .join('')}
        </div>
      </div>
      ${timelineChart(map, series, statsPeriod, coverage)}
      <div class="cal-nav">
        <button class="btn" id="statsPrev">← Prev</button>
        <h3>${esc(calendarTitle(cursor, statsPeriod))}</h3>
        <button class="btn" id="statsNext">Next →</button>
      </div>
      <h3 style="margin:18px 0 0;font-size:14px">Top this period</h3>
      ${tops.length
        ? /* HTML */ `
            <ol class="top-repos">
              ${tops
                .map(
                  (row, i) => /* HTML */ `
                    <li data-path="${esc(row.path)}" tabindex="0">
                      <span class="rank">${esc(i + 1)}</span>
                      <span class="name">${esc(row.name)}</span>
                      <span class="count">${esc(row.commits)}</span>
                    </li>
                  `,
                )
                .join('')}
            </ol>
          `
        : '<p class="note">No commits in this window across cached repositories.</p>'}
    </section>
    <section class="stats-section">
      <div class="section-head">
        <h2>Project, repository, and workspace hierarchy</h2>
      </div>
      <div class="board-wrap">
        <table>
          <thead>
            <tr>
              <th
                class="sortable ${boardSortKey === 'name'
                  ? boardSortDir > 0
                    ? 'sorted-asc'
                    : 'sorted-desc'
                  : ''}"
                data-board-sort="name"
              >
                Project / repository / workspace
              </th>
              <th
                class="sortable ${boardSortKey === 'commits7d'
                  ? boardSortDir > 0
                    ? 'sorted-asc'
                    : 'sorted-desc'
                  : ''}"
                data-board-sort="commits7d"
              >
                7d
              </th>
              <th
                class="sortable ${boardSortKey === 'commits30d'
                  ? boardSortDir > 0
                    ? 'sorted-asc'
                    : 'sorted-desc'
                  : ''}"
                data-board-sort="commits30d"
              >
                30d
              </th>
              <th
                class="sortable ${boardSortKey === 'commitCount'
                  ? boardSortDir > 0
                    ? 'sorted-asc'
                    : 'sorted-desc'
                  : ''}"
                data-board-sort="commitCount"
              >
                All
              </th>
              <th
                class="sortable ${boardSortKey === 'lastCommitAt'
                  ? boardSortDir > 0
                    ? 'sorted-asc'
                    : 'sorted-desc'
                  : ''}"
                data-board-sort="lastCommitAt"
              >
                Last commit
              </th>
              <th>30d spark</th>
            </tr>
          </thead>
          <tbody>
            ${hierarchyRows
              ? hierarchyRows
              : '<tr><td colspan="6" class="muted">No tracked repositories.</td></tr>'}
          </tbody>
        </table>
      </div>
    </section>
  `;
  bindStatsPage();
}
function paintLanguages() {
  const pulse = portfolioNumbers();
  const hasCache = pulse.cached > 0;
  $('languagesView').innerHTML = /* HTML */ `
    <div class="heading">
      <div>
        <h1>Languages</h1>
        <p>
          ${locMetric === 'complexity'
            ? 'Complexity by language across tracked repositories (scc).'
            : 'Code lines by language across tracked repositories (scc).'}
        </p>
      </div>
      <div class="actions">
        <button
          class="btn primary"
          id="languagesScan"
          ${!paired || busy || statsBusy || !paths.length ? 'disabled' : ''}
        >
          ${busy || statsBusy ? 'Scanning…' : '<span class="arrow">⟳</span> Scan'}
        </button>
      </div>
    </div>
    ${!paired
      ? '<div class="callout">Connect the companion to load language counts.</div>'
      : !paths.length
        ? '<div class="callout">Track repositories first, then Scan.</div>'
        : !hasCache
          ? '<div class="callout">No cached stats yet. Click Scan to compute LOC with scc.</div>'
          : ''}
    <section class="stats-section">
      <div class="section-head">
        <h2>${locMetric === 'complexity' ? 'Complexity' : 'Code lines'}</h2>
        <div class="period-tabs">
          <button data-loc-metric="code" class="${locMetric === 'code' ? 'selected' : ''}">
            Code
          </button>
          <button
            data-loc-metric="complexity"
            class="${locMetric === 'complexity' ? 'selected' : ''}"
          >
            Complexity
          </button>
        </div>
      </div>
      ${locMatrixHtml()}
    </section>
  `;
  bindLanguagesPage();
}
function bindLanguagesPage() {
  if ($('languagesScan')) $('languagesScan').onclick = runScan;
  document.querySelectorAll('[data-loc-metric]').forEach((button) => {
    button.onclick = () => {
      locMetric = button.dataset.locMetric;
      locSortKey = 'total';
      locSortDir = -1;
      paintLanguages();
    };
  });
  document.querySelectorAll('[data-loc-sort]').forEach((th) => {
    th.onclick = () => {
      const key = th.dataset.locSort;
      if (locSortKey === key) locSortDir = -locSortDir;
      else {
        locSortKey = key;
        locSortDir = key === 'name' ? 1 : -1;
      }
      paintLanguages();
    };
  });
  document.querySelectorAll('#languagesView [data-path]').forEach((node) => {
    node.onclick = () => openRepo(node.dataset.path);
    node.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openRepo(node.dataset.path);
      }
    };
  });
}
function bindStatsPage() {
  if ($('statsScan')) $('statsScan').onclick = runScan;
  document.querySelectorAll('[data-stats-project-toggle]').forEach((button) => {
    button.onclick = () => {
      const id = button.dataset.statsProjectToggle;
      if (statsExpandedProjects.has(id)) statsExpandedProjects.delete(id);
      else statsExpandedProjects.add(id);
      paintStats();
      restoreToggleFocus('[data-stats-project-toggle]', 'statsProjectToggle', id);
    };
  });
  document.querySelectorAll('[data-stats-repository-toggle]').forEach((button) => {
    button.onclick = () => {
      const id = button.dataset.statsRepositoryToggle;
      if (statsExpandedRepositories.has(id)) statsExpandedRepositories.delete(id);
      else statsExpandedRepositories.add(id);
      paintStats();
      restoreToggleFocus('[data-stats-repository-toggle]', 'statsRepositoryToggle', id);
    };
  });
  document.querySelectorAll('[data-stats-period]').forEach((button) => {
    button.onclick = () => {
      const period = button.dataset.statsPeriod;
      statsPeriod = period;
      statsCursor = null;
      paintStats();
      restoreToggleFocus('[data-stats-period]', 'statsPeriod', period);
    };
  });
  if ($('statsPrev')) {
    $('statsPrev').onclick = () => {
      shiftStatsCursor(-1);
      paintStats();
      $('statsPrev')?.focus();
    };
  }
  if ($('statsNext')) {
    $('statsNext').onclick = () => {
      shiftStatsCursor(1);
      paintStats();
      $('statsNext')?.focus();
    };
  }
  document.querySelectorAll('[data-board-sort]').forEach((th) => {
    th.onclick = () => {
      const key = th.dataset.boardSort;
      if (boardSortKey === key) boardSortDir = -boardSortDir;
      else {
        boardSortKey = key;
        boardSortDir = key === 'name' ? 1 : -1;
      }
      paintStats();
      restoreToggleFocus('[data-board-sort]', 'boardSort', key);
    };
  });
  const openRow = (node) => {
    const path = node?.dataset?.path;
    if (path) openRepo(path);
  };
  document.querySelectorAll('#statsView [data-path]').forEach((node) => {
    node.onclick = () => openRow(node);
    node.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openRow(node);
      }
    };
  });
}
function paintDetail() {
  const path = detailPath;
  const r = repos.find((repo) => repo.path === path) || {
    path,
    name: path.split(/[\\/]/).pop(),
    branch: '—',
    upstream: null,
    ahead: null,
    behind: null,
    modified: 0,
    untracked: 0,
    fresh: false,
    error: scannedAt ? null : 'Scan this machine to see sync status.',
    github: null,
  };
  const s = statsByPath[path];
  const remotes =
    (s?.remotes || []).map((m) => `${esc(m.name)} · ${esc(m.url)}`).join('<br>') || 'None';
  const summary = s?.activitySummary || {};
  const rangeLabel =
    detailPeriod === 'day'
      ? 'Month calendar · browse with Prev / Next'
      : detailPeriod === 'week'
        ? '12-week pages · browse with Prev / Next'
        : detailPeriod === 'month'
          ? 'Year of months · browse with Prev / Next'
          : '12-year pages · browse with Prev / Next';
  $('detailView').innerHTML = /* HTML */ `
    <button class="detail-back" id="backRepos">← Back to repositories</button>
    <h1 class="detail-title">${esc(r.name)}</h1>
    <p>${esc(r.branch)} → ${esc(r.upstream || 'No upstream')} · ${esc(next(r))}</p>
    <div class="mono" style="margin:8px 0 18px">${esc(r.path)}</div>
    <div class="detail-grid">
      ${cells([
        ['Behind', r.behind ?? 'Unknown'],
        ['Ahead', r.ahead ?? 'Unknown'],
        ['Modified', r.modified],
        ['Untracked', r.untracked],
      ])}
    </div>
    ${r.error ? `<div class="callout">${esc(r.error)}</div>` : ''}
    ${s
      ? /* HTML */ `
          <section class="stats-section">
            <h2>Volume & age</h2>
            <div class="detail-grid">
              ${cells([
                ['Total commits', s.commitCount ?? '—'],
                ['Age', s.ageLabel || '—'],
                ['First commit', formatDate(s.firstCommitAt)],
                ['Lifespan', s.lifespanDays != null ? s.lifespanDays + ' days' : '—'],
                ['7d commits', s.commits7d ?? '—'],
                ['30d commits', s.commits30d ?? '—'],
                ['90d commits', s.commits90d ?? '—'],
                ['Tracked size', formatBytes(s.workingTreeBytes)],
                ['Diff churn + / − / net', formatLines(s.linesTotal)],
                ['Diff churn 7d', formatLines(s.lines7d)],
                ['Diff churn 30d', formatLines(s.lines30d)],
                ['Diff churn 90d', formatLines(s.lines90d)],
              ])}
            </div>
            <p class="note">
              Last: ${esc(s.lastCommitAuthor || '—')} · ${esc(formatDate(s.lastCommitAt))} ·
              ${esc(s.lastCommitSubject || '')}
            </p>
          </section>
          <section class="stats-section">
            <h2>Commits over time</h2>
            <div class="detail-grid">
              ${cells([
                [
                  'Peak day',
                  (summary.peakDayCommits ?? '—') +
                    (summary.peakDayPeriod
                      ? ` · ${formatDate(summary.peakDayPeriod + 'T12:00:00')}`
                      : ''),
                ],
                ['Peak week', summary.peakWeekCommits ?? '—'],
                ['Avg / week', summary.avgWeekCommits ?? '—'],
                ['Burst ratio', summary.burstRatio ?? '—'],
                ['Active weeks', summary.activeWeeks ?? '—'],
                ['Quiet weeks', summary.quietWeeks ?? '—'],
              ])}
            </div>
            <div class="period-tabs">
              ${['day', 'week', 'month', 'year']
                .map(
                  (period) => /* HTML */ `
                    <button
                      data-period="${period}"
                      class="${detailPeriod === period ? 'selected' : ''}"
                    >
                      ${period[0].toUpperCase() + period.slice(1)}
                    </button>
                  `,
                )
                .join('')}
            </div>
            <p class="small">${esc(rangeLabel)}</p>
            ${timelineViz(s)}
          </section>
          <section class="stats-section">
            <h2>Remotes</h2>
            <div class="mono">${remotes}</div>
          </section>
          ${s.error ? `<div class="callout">${esc(s.error)}</div>` : ''}
          <p class="note">
            Stats computed ${esc(s.computedAt ? new Date(s.computedAt).toLocaleString() : '—')} ·
            HEAD ${esc(s.head || '—')}
          </p>
          <button class="btn" id="refreshOneStats">Refresh stats</button>
        `
      : /* HTML */ `
          <div class="callout">Loading repository stats…</div>
          <button class="btn lime" id="refreshOneStats">Load stats</button>
        `}
    ${r.github
      ? /* HTML */ `
          <p style="margin-top:16px">
            <a href="${esc(r.github)}" target="_blank" rel="noopener">Open on GitHub ↗</a>
          </p>
        `
      : ''}
  `;
  $('backRepos').onclick = closeRepo;
  document.querySelectorAll('[data-period]').forEach((button) => {
    button.onclick = () => {
      detailPeriod = button.dataset.period;
      detailCursor = null;
      paintDetail();
    };
  });
  if ($('calPrev'))
    $('calPrev').onclick = () => {
      shiftCursor(-1);
      paintDetail();
    };
  if ($('calNext'))
    $('calNext').onclick = () => {
      shiftCursor(1);
      paintDetail();
    };
  const refresh = $('refreshOneStats');
  if (refresh) {
    refresh.onclick = async () => {
      refresh.disabled = true;
      refresh.textContent = 'Computing…';
      try {
        const result = await api('stats', {
          path,
          refresh: !!statsByPath[path],
        });
        mergeStats(result.repositories, result.projects);
        render();
      } catch (e) {
        toast('Stats failed: ' + e.message);
        refresh.disabled = false;
        refresh.textContent = statsByPath[path] ? 'Refresh stats' : 'Load stats';
      }
    };
  }
}
async function ensureDetailStats() {
  if (!detailPath || !paired) return;
  const cached = statsByPath[detailPath];
  if (
    cached &&
    cached.commitsByDate &&
    typeof cached.commitsByDate === 'object' &&
    cached.activitySummary &&
    cached.linesByDate &&
    typeof cached.linesByDate === 'object' &&
    cached.linesTotal
  ) {
    return;
  }
  try {
    const result = await api('stats', { path: detailPath });
    mergeStats(result.repositories, result.projects);
    render();
  } catch (e) {
    toast('Stats failed: ' + e.message);
    if ($('detailView') && !$('detailView').hidden) {
      $('detailView').innerHTML = /* HTML */ `
        <button class="detail-back" id="backRepos">← Back to repositories</button>
        <div class="callout">${esc(e.message)}</div>
      `;
      $('backRepos').onclick = closeRepo;
    }
  }
}
async function syncRoute() {
  const route = parseRoute();
  detailPath = route.view === 'repo' ? route.path : null;
  render();
  if (detailPath) await ensureDetailStats();
}
function connect() {
  open(
    /* HTML */ `
      <h3 id="dialogTitle">Connect this machine</h3>
      <p>Easiest local path — starts companion, UI, and auto-pairs:</p>
      <div class="mono">python3 companion/server.py --root ~/Projects --ui</div>
      <p>
        Or paste a token from the companion terminal. The browser remembers it until you disconnect.
        Restarting the companion keeps the same token unless you pass
        <span class="mono">--new-token</span>
        .
      </p>
      <label class="small" for="pairToken">Pairing token</label>
      <input
        class="setup-input"
        id="pairToken"
        type="password"
        autocomplete="off"
        value="${esc(token)}"
      />
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
        <button class="btn lime" id="pair">Connect</button>
        ${paired ? '<button class="btn" id="forgetPair">Disconnect</button>' : ''}
      </div>
      <p id="pairError" class="note" role="status"></p>
      <p class="note">
        The browser may ask to access localhost. No login credentials or source files are sent to
        hosting.
      </p>
    `,
    'Connection settings',
  );
  $('pair').onclick = async () => {
    const candidate = $('pairToken').value.trim();
    if (!candidate) return;
    $('pair').disabled = true;
    try {
      statsByPath = {};
      statsProjects = [];
      await pairWithToken(candidate);
      render();
      close();
      await Promise.all([hydrateStats(), hydrateAiDetect()]);
      manage();
    } catch (e) {
      $('pairError').textContent =
        'Could not connect: ' +
        e.message +
        ' Check the companion and browser localhost permission.';
      $('pair').disabled = false;
    }
  };
  if ($('forgetPair')) {
    $('forgetPair').onclick = () => {
      clearPairing();
      render();
      close();
    };
  }
}
async function bootstrapPairing() {
  const params = new URLSearchParams(location.search);
  const fromQuery = (params.get('pair') || '').trim();
  if (fromQuery) {
    params.delete('pair');
    const qs = params.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    try {
      await pairWithToken(fromQuery);
      render();
      await Promise.all([hydrateStats(), hydrateAiDetect()]);
      return;
    } catch {
      writeStoredToken('');
    }
  }
  const stored = readStoredToken();
  if (!stored) return;
  try {
    await pairWithToken(stored);
    render();
    await Promise.all([hydrateStats(), hydrateAiDetect()]);
  } catch {
    writeStoredToken('');
    token = '';
    paired = false;
  }
}
function projectManagerModel(trackedPaths, canonicalWorkspaces, discovered) {
  const tracked = new Set(trackedPaths || []);
  const model = new Map();
  const mergeWorkspace = (item) => {
    const workspaceId = item?.workspace_id || item?.path;
    if (!workspaceId) return;
    const existing = model.get(workspaceId) || {};
    const path = item.path || existing.path || workspaceId;
    model.set(workspaceId, {
      ...existing,
      ...item,
      workspace_id: workspaceId,
      workspace_name:
        item.workspace_name || item.name || existing.workspace_name || path.split(/[\\/]/).pop(),
      path,
      repository_id: item.repository_id || existing.repository_id || null,
      repository_name: item.repository_name || existing.repository_name || null,
      project_id: item.project_id || existing.project_id || null,
      project_name: item.project_name || existing.project_name || null,
      tracked: tracked.has(path) || tracked.has(workspaceId),
    });
  };
  (canonicalWorkspaces || []).forEach(mergeWorkspace);
  (discovered || []).forEach(mergeWorkspace);
  tracked.forEach((path) => {
    if (!model.has(path)) {
      model.set(path, {
        workspace_id: path,
        workspace_name: path.split(/[\\/]/).pop(),
        path,
        repository_id: null,
        repository_name: null,
        project_id: null,
        project_name: null,
        tracked: true,
      });
    }
  });
  return model;
}

function configuredProjectForRepository(identityConfig, repositoryId) {
  if (!repositoryId) return null;
  return (identityConfig?.projects || []).find((project) =>
    (project.repositories || []).includes(repositoryId),
  );
}

function projectManagerRows(
  choices,
  selectedRepositoryIds,
  identityConfig,
  trackingPending = false,
) {
  const rows = [...choices.values()].sort((a, b) =>
    String(a.workspace_name || a.path).localeCompare(String(b.workspace_name || b.path)),
  );
  const trackedRepositoryIds = new Set(
    rows
      .filter((row) => row.tracked)
      .map((row) => row.repository_id)
      .filter(Boolean),
  );
  if (!rows.length) return '<p class="small">Discover a folder or add a path.</p>';
  return rows
    .map((row) => {
      const configured = configuredProjectForRepository(identityConfig, row.repository_id);
      const hasCurrentConfig = identityConfig !== null;
      const projectId =
        configured?.id || (hasCurrentConfig ? row.repository_id : row.project_id) || '';
      const projectName =
        configured?.name ||
        (hasCurrentConfig ? row.repository_name : row.project_name) ||
        'Unresolved';
      const repositoryName = row.repository_name || row.workspace_name || row.path;
      const groupingDisabled =
        trackingPending ||
        !identityConfig ||
        !row.repository_id ||
        !trackedRepositoryIds.has(row.repository_id) ||
        !projectIdentityConfigEditable(identityConfig);
      const groupingLabel = `Select ${repositoryName} for grouping`;
      const trackingLabel = `Track ${row.workspace_name || row.path}`;
      return /* HTML */ `
        <div class="project-manager-row" data-current-project="${esc(projectId)}">
          <div class="project-row-meta">
            <strong>${esc(row.workspace_name || row.path)}</strong>
            <span class="small">${esc(row.path)}</span>
            <span class="small">
              Repository: ${esc(row.repository_name || 'Identity unavailable')}
            </span>
            <span class="small">Project: ${esc(projectName)}</span>
          </div>
          <div class="project-row-actions">
            <label class="project-group-select">
              <input
                type="checkbox"
                data-group-repository="${esc(row.repository_id || '')}"
                ${row.repository_id && selectedRepositoryIds.has(row.repository_id)
                  ? 'checked'
                  : ''}
                aria-label="${esc(groupingLabel)}"
                ${groupingDisabled ? 'disabled' : ''}
              />
              <span>Group</span>
            </label>
            <label class="project-track-toggle">
              <input
                type="checkbox"
                data-track-workspace="${esc(row.workspace_id)}"
                aria-label="${esc(trackingLabel)}"
                ${row.tracked ? 'checked' : ''}
                ${trackingPending ? 'disabled' : ''}
              />
              <span>Tracked</span>
            </label>
          </div>
        </div>
      `;
    })
    .join('');
}

function projectBulkEditor(config, selectedRepositoryIds, mode = 'existing') {
  const count = new Set(selectedRepositoryIds || []).size;
  const projects = config?.projects || [];
  const activeMode = mode === 'new' || mode === 'standalone' ? mode : 'existing';
  return /* HTML */ `
    <div class="project-editor" data-project-editor="bulk">
      <strong>${count} ${count === 1 ? 'repository' : 'repositories'} selected</strong>
      <label class="small" for="projectMode">Assignment</label>
      <select id="projectMode">
        <option value="existing" ${activeMode === 'existing' ? 'selected' : ''}>
          Existing project
        </option>
        <option value="new" ${activeMode === 'new' ? 'selected' : ''}>New project</option>
        <option value="standalone" ${activeMode === 'standalone' ? 'selected' : ''}>
          Standalone
        </option>
      </select>
      <label class="small" for="projectTarget" ${activeMode === 'existing' ? '' : 'hidden'}>
        Project
      </label>
      <select id="projectTarget" ${activeMode === 'existing' ? '' : 'hidden'}>
        ${projects
          .map((project) => `<option value="${esc(project.id)}">${esc(project.name)}</option>`)
          .join('')}
      </select>
      <label class="small" for="projectName" ${activeMode === 'new' ? '' : 'hidden'}>
        New project name
      </label>
      <input id="projectName" ${activeMode === 'new' ? '' : 'hidden'} autocomplete="off" />
      <div class="project-row-actions">
        <button class="btn primary" type="button" id="projectApply">Apply</button>
        <button class="btn" type="button" id="projectCancel">Cancel</button>
      </div>
    </div>
  `;
}

function configuredProjectCards(config, visibleRepositoryIds, editingDisabled = false) {
  const visibleOccurrences = [...(visibleRepositoryIds || [])].filter(Boolean);
  const visible = new Set(visibleOccurrences);
  const projects = config?.projects || [];
  if (!projects.length) return '<p class="small">No configured projects yet.</p>';
  return projects
    .map((project) => {
      const repositories = project.repositories || [];
      const visibleWorkspaceCount = visibleOccurrences.filter((repositoryId) =>
        repositories.includes(repositoryId),
      ).length;
      const hiddenRepositoryCount = repositories.filter(
        (repositoryId) => !visible.has(repositoryId),
      ).length;
      const primaryMappingCount = Object.keys(project.primary_workspaces || {}).length;
      const protectedMappings = (project.workspace_aliases || []).length || primaryMappingCount;
      return /* HTML */ `
        <section class="configured-project-card" data-configured-project="${esc(project.id)}">
          <div>
            <strong>${esc(project.name)}</strong>
            <p class="small">
              ${repositories.length} ${repositories.length === 1 ? 'repository' : 'repositories'} ·
              ${visibleWorkspaceCount} visible
              ${visibleWorkspaceCount === 1 ? 'workspace' : 'workspaces'}
              ${hiddenRepositoryCount ? ` · ${hiddenRepositoryCount} not currently visible` : ''}
            </p>
          </div>
          <div class="project-row-actions">
            <button
              class="btn"
              type="button"
              data-rename-project="${esc(project.id)}"
              ${editingDisabled ? 'disabled' : ''}
            >
              Rename
            </button>
            <button
              class="btn"
              type="button"
              data-ungroup-project="${esc(project.id)}"
              ${repositories.length && !primaryMappingCount && !editingDisabled ? '' : 'disabled'}
            >
              Ungroup repositories
            </button>
            <button
              class="btn"
              type="button"
              data-delete-project="${esc(project.id)}"
              ${protectedMappings || editingDisabled ? 'disabled' : ''}
            >
              Delete project
            </button>
          </div>
          ${protectedMappings
            ? `<p class="small">${
                primaryMappingCount ? 'Ungroup and delete' : 'Delete'
              } is unavailable while historical aliases or primary workspaces are protected.</p>`
            : ''}
          <div data-project-inline-editor="${esc(project.id)}"></div>
        </section>
      `;
    })
    .join('');
}

function projectLifecycleEditor(project, action) {
  if (action === 'rename') {
    return /* HTML */ `
      <div class="project-editor" data-project-editor="rename">
        <label class="small" for="projectRenameName">Project name</label>
        <input id="projectRenameName" value="${esc(project.name)}" autocomplete="off" />
        <div class="project-row-actions">
          <button class="btn primary" type="button" data-confirm-project-action="rename">
            Save name
          </button>
          <button class="btn" type="button" data-cancel-project-action>Cancel</button>
        </div>
      </div>
    `;
  }
  const verb = action === 'delete' ? 'Delete project' : 'Make repositories standalone';
  return /* HTML */ `
    <div class="project-editor" data-project-editor="${esc(action)}">
      <p class="small">
        ${action === 'delete'
          ? 'The repositories remain tracked and return to standalone projects.'
          : 'All repositories in this project will return to standalone projects.'}
      </p>
      <div class="project-row-actions">
        <button class="btn primary" type="button" data-confirm-project-action="${esc(action)}">
          ${verb}
        </button>
        <button class="btn" type="button" data-cancel-project-action>Cancel</button>
      </div>
    </div>
  `;
}

function bindProjectManagerRows(
  choices,
  selectedRepositoryIds,
  trackingChanged,
  redraw,
  openProjectPane = () => {},
) {
  document.querySelectorAll('[data-track-workspace]').forEach((control) => {
    control.onchange = () => {
      const row = choices.get(control.dataset.trackWorkspace);
      if (!row) return;
      row.tracked = control.checked;
      if (!row.tracked && row.repository_id) {
        selectedRepositoryIds.delete(row.repository_id);
      }
      trackingChanged();
    };
  });
  document.querySelectorAll('[data-group-repository]').forEach((control) => {
    control.onchange = () => {
      const repositoryId = control.dataset.groupRepository;
      if (!repositoryId) return;
      if (control.checked) selectedRepositoryIds.add(repositoryId);
      else selectedRepositoryIds.delete(repositoryId);
      if (selectedRepositoryIds.size) openProjectPane();
      redraw();
    };
  });
}

function stageProjectManagerAction(config, action) {
  let draft;
  if (action.type === 'assign') {
    draft = assignRepositories(config, action.repositoryIds, action.target).config;
  } else if (action.type === 'standalone') {
    draft = makeRepositoriesStandalone(config, action.repositoryIds);
  } else if (action.type === 'rename') {
    draft = renameConfiguredProject(config, action.projectId, action.name);
  } else if (action.type === 'ungroup') {
    const project = (config.projects || []).find((item) => item.id === action.projectId);
    if (!project) throw new Error('Project no longer exists.');
    draft = makeRepositoriesStandalone(config, project.repositories || []);
  } else if (action.type === 'delete') {
    draft = deleteConfiguredProject(config, action.projectId);
  } else {
    throw new Error('Unknown project action.');
  }
  return draft;
}

function projectManagerTrackedPaths(choices) {
  return [...choices.values()]
    .filter((row) => row.tracked)
    .map((row) => row.path)
    .sort();
}

function projectManagerDraftChanged(state) {
  const originalPaths = [...(state.originalPaths || [])].sort();
  const trackedPaths = projectManagerTrackedPaths(state.choices);
  const originalProjects = state.originalConfig?.projects || [];
  const draftProjects = state.draft?.projects || [];
  return (
    JSON.stringify(originalPaths) !== JSON.stringify(trackedPaths) ||
    JSON.stringify(originalProjects) !== JSON.stringify(draftProjects)
  );
}

function requestProjectManagerClose(dirty, showConfirmation, closeDrawer) {
  if (dirty) {
    showConfirmation();
    return 'confirm';
  }
  closeDrawer();
  return 'closed';
}

async function commitProjectManagerDraft(state, hooks = {}) {
  const trackedPaths = projectManagerTrackedPaths(state.choices);
  const originalPaths = [...(state.originalPaths || [])].sort();
  const selectionChanged = JSON.stringify(trackedPaths) !== JSON.stringify(originalPaths);
  const identityChanged =
    JSON.stringify(state.draft?.projects || []) !==
    JSON.stringify(state.originalConfig?.projects || []);
  const saveSelection =
    hooks.saveSelection || ((nextPaths) => api('selection', { paths: nextPaths }));
  const loadIdentity = hooks.loadIdentity || (() => api('project-identities'));
  const saveIdentity = hooks.saveIdentity || saveProjectIdentityDraft;
  const applySelectionStatus = hooks.applySelectionStatus || applyStatus;
  const invalidate =
    hooks.invalidate ||
    (() => {
      invalidateTrackedScopeData();
      aiUsageInvalidatedByIdentity = true;
      gitStatsInvalidatedByIdentity = true;
      render();
    });
  const closeDrawer = hooks.closeDrawer || close;
  const refresh = hooks.refresh || (() => runScan({ throwOnError: true }));

  if (!selectionChanged && !identityChanged) {
    closeDrawer();
    return { status: 'unchanged', config: state.originalConfig };
  }

  let currentConfig = state.originalConfig;
  if (selectionChanged) {
    const selection = await saveSelection(trackedPaths);
    applySelectionStatus(selection);
    currentConfig = await loadIdentity();
  }

  let saved = currentConfig;
  if (identityChanged) {
    try {
      saved = await saveIdentity(state.draft, currentConfig.fingerprint);
    } catch (error) {
      if (error.status === 409 && error.code === 'identity_config_conflict') {
        return { status: 'conflict', config: await loadIdentity(), error };
      }
      throw error;
    }
  }

  invalidate();
  closeDrawer();
  try {
    if (trackedPaths.length) await refresh();
    return { status: 'saved', config: saved };
  } catch (error) {
    toast(`Changes saved; scan failed. Use Scan to retry: ${error.message}`);
    if (hooks.onRefreshError) hooks.onRefreshError(error);
    return { status: 'refresh_failed', config: saved, error };
  }
}

function showProjectManagerError(error) {
  $('settingsError').textContent = error?.message || String(error);
}

function projectIdentityConfigEditable(config) {
  return !(config?.warnings || []).some((warning) => warning.code === 'invalid_identity_config');
}

function projectIdentityWarningsMessage(config) {
  const warnings = config?.warnings || [];
  if (!warnings.length) return 'Project grouping is ready.';
  if (!projectIdentityConfigEditable(config)) {
    return 'Project grouping file is invalid; grouping editing is disabled until the file is fixed.';
  }
  const details = warnings.map((warning) =>
    [warning.code, warning.workspace_id].filter(Boolean).join(' — '),
  );
  return `Project grouping loaded with warnings: ${details.join('; ')}.`;
}

async function runProjectManagerSaveOnce(state, operation, onSaving = () => {}) {
  if (state.saving) return { status: 'busy' };
  state.saving = true;
  onSaving(true);
  try {
    return await operation();
  } finally {
    state.saving = false;
    onSaving(false);
  }
}

function setProjectManagerSaving(saving) {
  document
    .querySelectorAll('#drawerBody button, #drawerBody input, #drawerBody select')
    .forEach((control) => {
      if (saving) {
        projectManagerDisabledState.set(control, control.disabled);
        control.disabled = true;
      } else if (projectManagerDisabledState.has(control)) {
        control.disabled = projectManagerDisabledState.get(control);
        projectManagerDisabledState.delete(control);
      }
    });
}

function restoreProjectManagerFocus(selector) {
  document.querySelector(selector)?.focus();
}

function focusFirstProjectEditorControl(selector) {
  document
    .querySelector(selector)
    ?.querySelector('input:not([type="hidden"]), select, button:not(:disabled)')
    ?.focus();
}

function saveProjectIdentityDraft(draft, fingerprint) {
  return api('project-identities', {
    version: draft.version,
    projects: draft.projects,
    expected_fingerprint: fingerprint,
  });
}

async function manage() {
  if (!paired) return connect();
  const originalPaths = [...paths];
  let discoveredWorkspaces = [];
  let choices = projectManagerModel(paths, workspaces, discoveredWorkspaces);
  let originalConfig = null;
  let identityDraft = null;
  const selectedRepositoryIds = new Set();
  let bulkEditorMode = null;
  let bulkAssignmentMode = 'existing';
  let lifecycleEditor = null;
  let projectPaneOpen = false;
  let closeConfirmationOpen = false;
  const projectSaveState = { saving: false };
  open(
    /* HTML */ `
      <h3 id="dialogTitle">Manage tracked repos</h3>
      <p>
        Stage repository tracking and project grouping here. Nothing is saved or scanned until you
        choose Save & scan.
      </p>
      <div class="project-manager-layout">
        <section class="project-manager-repositories">
          <label class="small" for="rootPath">Discovery folder</label>
          <input class="setup-input" id="rootPath" value="${esc(roots[0] || '')}" />
          <div class="choice-toolbar">
            <button class="btn" id="discover">Discover repositories</button>
            <button class="btn" type="button" id="selectAll">Track all</button>
            <button class="btn" type="button" id="deselectAll">Untrack all</button>
            <button class="btn" type="button" id="showProjects">Projects</button>
          </div>
          <div id="choices" class="choice-list"></div>
          <label class="small" for="repoPath">Or add a repository by full path</label>
          <input class="setup-input" id="repoPath" placeholder="/path/to/a/repository" />
          <button class="btn" id="addPath">Add path</button>
          <p class="note">
            Approved folders: ${roots.map(esc).join(', ')}. Restart with another --root to approve
            an additional folder.
          </p>
        </section>
        <aside id="projectManagerPane" class="project-manager-pane" hidden>
          <div id="projectManagerActions" class="project-manager-actions"></div>
        </aside>
      </div>
      <div class="project-manager-footer">
        <p class="note" role="status" id="settingsError"></p>
        <div id="projectCloseConfirm" class="project-close-confirm" hidden>
          <strong>Save your staged changes?</strong>
          <p class="small">Closing now would discard repository and project changes.</p>
          <div class="project-row-actions">
            <button class="btn primary" type="button" id="confirmSaveProjectManager">
              Save & scan
            </button>
            <button class="btn" type="button" id="discardProjectManager">Discard</button>
            <button class="btn" type="button" id="continueProjectManager">Continue editing</button>
          </div>
        </div>
        <div class="project-row-actions" id="projectManagerFooterActions">
          <button class="btn" type="button" id="cancelProjectManager">Cancel</button>
          <button class="btn primary" type="button" id="saveProjectManager" disabled>
            Save & scan
          </button>
        </div>
      </div>
    `,
    'Tracking settings',
  );
  $('drawerBody').classList.add('project-manager-body');
  document.querySelector('.drawer')?.classList.add('project-manager-drawer');

  const managerState = () => ({
    originalPaths,
    choices,
    originalConfig,
    draft: identityDraft,
  });
  const isDirty = () =>
    !!originalConfig && !!identityDraft && projectManagerDraftChanged(managerState());
  const closeManagerNow = () => {
    drawerCloseHandler = null;
    close();
  };
  drawerCloseHandler = () =>
    requestProjectManagerClose(
      isDirty(),
      () => {
        closeConfirmationOpen = true;
        draw();
      },
      () => {},
    ) !== 'confirm';

  const draw = () => {
    $('choices').innerHTML = projectManagerRows(choices, selectedRepositoryIds, identityDraft);
    bindProjectManagerRows(choices, selectedRepositoryIds, draw, draw, () => {
      projectPaneOpen = true;
      bulkEditorMode = 'bulk';
    });
    $('projectManagerPane').hidden = !projectPaneOpen;
    $('projectCloseConfirm').hidden = !closeConfirmationOpen;
    $('projectManagerFooterActions').hidden = closeConfirmationOpen;
    $('saveProjectManager').disabled = !isDirty() || projectSaveState.saving;
    if (!identityDraft) {
      $('projectManagerActions').innerHTML = '<p class="small">Loading project grouping…</p>';
      return;
    }
    const visibleRepositoryIds = [...choices.values()]
      .filter((row) => row.tracked)
      .map((row) => row.repository_id);
    const groupingLocked = !projectIdentityConfigEditable(identityDraft);
    if (
      bulkEditorMode === 'bulk' &&
      bulkAssignmentMode === 'existing' &&
      !(identityDraft.projects || []).length
    ) {
      bulkAssignmentMode = 'new';
    }
    $('projectManagerActions').innerHTML = /* HTML */ `
      <div class="project-bulk-toolbar">
        <strong>${selectedRepositoryIds.size} selected</strong>
        <div class="project-row-actions">
          <button
            class="btn"
            type="button"
            id="clearProjectSelection"
            ${selectedRepositoryIds.size ? '' : 'disabled'}
          >
            Clear selection
          </button>
        </div>
      </div>
      ${bulkEditorMode === 'bulk'
        ? projectBulkEditor(identityDraft, selectedRepositoryIds, bulkAssignmentMode)
        : ''}
      <h4>Configured projects</h4>
      <div class="configured-project-list">
        ${configuredProjectCards(identityDraft, visibleRepositoryIds, groupingLocked)}
      </div>
    `;
    if (lifecycleEditor) {
      const project = (identityDraft.projects || []).find(
        (item) => item.id === lifecycleEditor.projectId,
      );
      const container = document.querySelector(
        `[data-project-inline-editor="${lifecycleEditor.projectId}"]`,
      );
      if (project && container) {
        container.innerHTML = projectLifecycleEditor(project, lifecycleEditor.type);
      }
    }
    bindProjectManagerActions();
  };
  const completeProjectAction = (action, focusSelector) => {
    try {
      identityDraft = stageProjectManagerAction(identityDraft, action);
      bulkEditorMode = null;
      lifecycleEditor = null;
      selectedRepositoryIds.clear();
      closeConfirmationOpen = false;
      draw();
      $('settingsError').textContent = 'Change staged. Save & scan when you are finished.';
    } catch (error) {
      showProjectManagerError(error);
      restoreProjectManagerFocus(focusSelector);
    }
  };
  const bindProjectManagerActions = () => {
    $('clearProjectSelection').onclick = () => {
      selectedRepositoryIds.clear();
      bulkEditorMode = null;
      draw();
      restoreProjectManagerFocus('#showProjects');
    };
    if (bulkEditorMode === 'bulk') {
      $('projectMode').onchange = () => {
        bulkAssignmentMode = $('projectMode').value;
        draw();
        focusFirstProjectEditorControl('[data-project-editor="bulk"]');
      };
      $('projectCancel').onclick = () => {
        bulkEditorMode = null;
        draw();
        restoreProjectManagerFocus('#showProjects');
      };
      $('projectApply').onclick = () => {
        if (!selectedRepositoryIds.size) {
          showProjectManagerError(new Error('Select at least one repository.'));
          return;
        }
        if (bulkAssignmentMode === 'standalone') {
          completeProjectAction(
            { type: 'standalone', repositoryIds: [...selectedRepositoryIds] },
            '#showProjects',
          );
          return;
        }
        const target =
          bulkAssignmentMode === 'new'
            ? { name: $('projectName').value }
            : { projectId: $('projectTarget').value };
        completeProjectAction(
          { type: 'assign', repositoryIds: [...selectedRepositoryIds], target },
          '#showProjects',
        );
      };
    }
    document.querySelectorAll('[data-rename-project]').forEach((button) => {
      button.onclick = () => {
        lifecycleEditor = { type: 'rename', projectId: button.dataset.renameProject };
        bulkEditorMode = null;
        draw();
        focusFirstProjectEditorControl('[data-project-editor="rename"]');
      };
    });
    document.querySelectorAll('[data-ungroup-project]').forEach((button) => {
      button.onclick = () => {
        lifecycleEditor = { type: 'ungroup', projectId: button.dataset.ungroupProject };
        bulkEditorMode = null;
        draw();
        focusFirstProjectEditorControl('[data-project-editor="ungroup"]');
      };
    });
    document.querySelectorAll('[data-delete-project]').forEach((button) => {
      button.onclick = () => {
        lifecycleEditor = { type: 'delete', projectId: button.dataset.deleteProject };
        bulkEditorMode = null;
        draw();
        focusFirstProjectEditorControl('[data-project-editor="delete"]');
      };
    });
    const cancelLifecycle = document.querySelector('[data-cancel-project-action]');
    if (cancelLifecycle && lifecycleEditor) {
      cancelLifecycle.onclick = () => {
        const selector = `[data-${lifecycleEditor.type}-project="${lifecycleEditor.projectId}"]`;
        lifecycleEditor = null;
        draw();
        restoreProjectManagerFocus(selector);
      };
    }
    const confirmLifecycle = document.querySelector('[data-confirm-project-action]');
    if (confirmLifecycle && lifecycleEditor) {
      confirmLifecycle.onclick = () => {
        const action = lifecycleEditor;
        const focusSelector = `[data-${action.type}-project="${action.projectId}"]`;
        if (action.type === 'rename') {
          completeProjectAction(
            {
              type: 'rename',
              projectId: action.projectId,
              name: $('projectRenameName').value,
            },
            focusSelector,
          );
        } else {
          completeProjectAction({ type: action.type, projectId: action.projectId }, focusSelector);
        }
      };
    }
  };
  draw();
  $('showProjects').onclick = () => {
    projectPaneOpen = true;
    draw();
  };
  $('selectAll').onclick = () => {
    choices.forEach((row) => {
      row.tracked = true;
    });
    draw();
  };
  $('deselectAll').onclick = () => {
    choices.forEach((row) => {
      row.tracked = false;
    });
    draw();
  };
  $('discover').onclick = async () => {
    $('discover').disabled = true;
    try {
      const result = await api('discover', { root: $('rootPath').value });
      discoveredWorkspaces = [...discoveredWorkspaces, ...(result.repositories || [])];
      choices = projectManagerModel(
        projectManagerTrackedPaths(choices),
        workspaces,
        discoveredWorkspaces,
      );
      draw();
      const excluded = Number(result.excluded_candidate_count || 0);
      $('settingsError').textContent =
        `Found ${(result.repositories || []).length} eligible GitHub repositories.` +
        (excluded
          ? ` Excluded ${excluded} candidate${excluded === 1 ? '' : 's'} without a github.com remote.`
          : '');
    } catch (e) {
      $('settingsError').textContent = e.message;
    } finally {
      $('discover').disabled = false;
    }
  };
  $('addPath').onclick = () => {
    const path = $('repoPath').value.trim();
    if (path) {
      choices.set(path, {
        workspace_id: path,
        workspace_name: path.split(/[\\/]/).pop(),
        path,
        repository_id: null,
        repository_name: null,
        project_id: null,
        project_name: null,
        tracked: true,
      });
      draw();
      $('repoPath').value = '';
    }
  };
  const saveAndScan = async () => {
    await runProjectManagerSaveOnce(
      projectSaveState,
      async () => {
        try {
          const outcome = await commitProjectManagerDraft(managerState(), {
            closeDrawer: closeManagerNow,
          });
          if (outcome.status === 'conflict') {
            originalConfig = outcome.config;
            identityDraft = JSON.parse(JSON.stringify(outcome.config));
            closeConfirmationOpen = false;
            draw();
            $('settingsError').textContent =
              'Project grouping changed in another editor. The latest configuration was loaded; repeat your changes.';
          }
          return outcome;
        } catch (error) {
          showProjectManagerError(error);
          return { status: 'error', error };
        }
      },
      setProjectManagerSaving,
    );
  };
  $('saveProjectManager').onclick = saveAndScan;
  $('confirmSaveProjectManager').onclick = saveAndScan;
  $('cancelProjectManager').onclick = close;
  $('discardProjectManager').onclick = closeManagerNow;
  $('continueProjectManager').onclick = () => {
    closeConfirmationOpen = false;
    draw();
  };
  try {
    originalConfig = await api('project-identities');
    identityDraft = JSON.parse(JSON.stringify(originalConfig));
    draw();
    $('settingsError').textContent = projectIdentityWarningsMessage(identityDraft);
  } catch (error) {
    $('settingsError').textContent = `Could not load project grouping: ${error.message}`;
  }
}
async function runScan(options = {}) {
  if (!paired || busy || statsBusy || !paths.length) {
    if (options?.throwOnError) {
      throw new Error(
        !paired
          ? 'Companion is not connected.'
          : !paths.length
            ? 'No repositories are tracked.'
            : 'Another scan is already running.',
      );
    }
    return;
  }
  busy = true;
  statsBusy = true;
  render();
  try {
    const result = await api('scan', {});
    repos = result.repositories;
    workspaces = Array.isArray(result.repositories) ? result.repositories : workspaces;
    machineName = result.machine;
    scannedAt = result.scannedAt;
    activity.unshift(
      `Scanned ${repos.length} repositories at ${new Date(scannedAt).toLocaleString()}`,
    );
    const stats = await api('stats', { all: true, refresh: true });
    mergeStats(stats.repositories, stats.projects);
    gitStatsInvalidatedByIdentity = false;
    activity.unshift(
      `Loaded portfolio stats for ${(stats.repositories || []).length} repositories`,
    );
    const errors = repos.filter((r) => r.error).length;
    toast(
      `Scan complete · ${(stats.repositories || []).length} stats updated` +
        (errors ? ` · ${errors} need Git access checks` : ''),
    );
  } catch (e) {
    toast('Scan failed: ' + e.message);
    if (options?.throwOnError) throw e;
  } finally {
    busy = false;
    statsBusy = false;
    render();
  }
}
$('scan').onclick = runScan;
$('connect').onclick = connect;
$('disconnect').onclick = () => {
  clearPairing();
  if (detailPath) closeRepo();
  render();
};
$('manage').onclick = manage;
$('close').onclick = close;
$('overlay').onclick = (e) => {
  if (e.target === $('overlay')) close();
};
document.addEventListener('keydown', (e) => {
  if (!$('overlay').classList.contains('open')) return;
  if (e.key === 'Escape') close();
  if (e.key === 'Tab') {
    const nodes = [...$('overlay').querySelectorAll('button:not(:disabled),a,input,select')];
    const first = nodes[0],
      last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
});
$('search').oninput = render;
document.querySelectorAll('#repoHead th.sortable').forEach((th) => {
  th.onclick = () => {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir = -sortDir;
    else {
      sortKey = key;
      sortDir = key === 'name' || key === 'branch' || key === 'next' ? 1 : -1;
    }
    render();
  };
});
document.querySelectorAll('[data-filter]').forEach(
  (b) =>
    (b.onclick = () => {
      filter = b.dataset.filter;
      document
        .querySelectorAll('[data-filter]')
        .forEach((x) => x.classList.toggle('selected', x === b));
      render();
    }),
);
$('overviewNav').onclick = () => {
  if (parseRoute().view !== 'overview') location.hash = '#/';
  else render();
};
$('statsNav').onclick = () => openStats();
$('languagesNav').onclick = () => openLanguages();
$('aiNav').onclick = () => openAi();
window.addEventListener('hashchange', () => {
  syncRoute();
});
$('activityNav').onclick = () =>
  open(
    /* HTML */ `
      <h3 id="dialogTitle">This session</h3>
      <p>${activity.map(esc).join('<br>') || 'No scans yet.'}</p>
    `,
    'Activity',
  );
syncRoute();
bootstrapPairing();
