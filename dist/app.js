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
  token = '',
  lastFocus,
  activity = [],
  busy = false,
  statsBusy = false,
  statsByPath = {},
  detailPath = null,
  detailPeriod = 'day',
  detailCursor = null,
  statsPeriod = 'day',
  statsCursor = null,
  boardSortKey = 'commits30d',
  boardSortDir = -1,
  sortKey = 'name',
  sortDir = 1;
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
  repos = [];
  scannedAt = null;
  statsByPath = {};
  writeStoredToken('');
}
function applyStatus(result) {
  machineName = result.machine || machineName;
  roots = result.roots || [];
  paths = result.paths || [];
  if (Array.isArray(result.repositories)) repos = result.repositories;
  if ('scannedAt' in result) scannedAt = result.scannedAt || null;
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
  const long = path === 'scan' || (path === 'stats' && body);
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
    if (!response.ok) throw Error(result.error || 'Companion request failed.');
    return result;
  } finally {
    clearTimeout(timer);
  }
}
function mergeStats(list) {
  (list || []).forEach((s) => {
    if (s?.path) statsByPath[s.path] = s;
  });
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
    th.setAttribute(
      'aria-sort',
      active ? (sortDir > 0 ? 'ascending' : 'descending') : 'none',
    );
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
    mergeStats(result.repositories);
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
  lastFocus = document.activeElement;
  $('drawerLabel').textContent = label;
  $('drawerBody').innerHTML = body;
  $('overlay').classList.add('open');
  $('close').focus();
}
function close() {
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
  const other = $('machine').value === 'other';
  const list = other
    ? []
    : repos
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
          <td>
            ${dirty ? `${dirty} ${dirty === 1 ? 'file' : 'files'}` : '✓ Clean'}
          </td>
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
  $('empty').textContent = other
    ? 'No report from a second machine. Open this dashboard on that computer and pair its companion.'
    : !paired
      ? 'Connect the local companion to begin.'
      : !scannedAt
        ? 'Choose repositories under Manage tracked repos, then scan this machine.'
        : 'No repositories match this view.';
  $('repoCount').textContent = other ? 'Unknown' : list.length;
  ['total', 'behindStat', 'aheadStat', 'dirtyStat'].forEach(
    (id, i) =>
      ($(id).textContent =
        other || !scannedAt
          ? '—'
          : [
              repos.length,
              repos.filter((r) => r.behind > 0).length,
              repos.filter((r) => r.ahead > 0).length,
              repos.filter((r) => r.modified + r.untracked > 0).length,
            ][i]),
  );
  const pulse = portfolioNumbers();
  const showPortfolio = paired && !other && paths.length;
  $('portfolioStats').hidden = !showPortfolio;
  if (showPortfolio) {
    const hasCache = pulse.cached > 0;
    $('commitStat').textContent = hasCache ? String(pulse.commits) : '—';
    $('commit30Stat').textContent = hasCache ? String(pulse.commits30) : '—';
    $('contributorStat').textContent = hasCache ? String(pulse.contributors) : '—';
    $('statsCoverageStat').textContent = `${pulse.cached}/${pulse.tracked}`;
    $('statsCoverageLabel').textContent = hasCache
      ? 'Cached / tracked'
      : statsBusy || busy
        ? 'Scanning…'
        : 'Run Scan to fill stats';
  }
  $('machineTitle').innerHTML = other
    ? 'Second machine <span>NOT SCANNED</span>'
    : esc(machineName) + ` <span>${paired ? 'CONNECTED' : 'NOT CONNECTED'}</span>`;
  $('machineMeta').textContent = other
    ? 'No report received'
    : paired
      ? `${paths.length} tracked repos · ${scannedAt ? 'Scanned ' + new Date(scannedAt).toLocaleString() : 'Not scanned yet'}`
      : 'Run companion with --ui, or connect with a pairing token.';
  $('snapshotLabel').textContent =
    scannedAt && !other
      ? 'Compared with each branch’s upstream · ' + new Date(scannedAt).toLocaleString()
      : 'No live scan yet';
  $('currentInsights').hidden = other || !scannedAt;
  $('scan').disabled = other || !paired || busy || statsBusy || !paths.length;
  $('manage').disabled = other || !paired || busy || statsBusy;
  $('connect').textContent = paired ? 'Connection settings' : 'Connect companion';
  if ($('disconnect')) $('disconnect').hidden = !paired;
  $('scan').innerHTML =
    busy || statsBusy ? 'Scanning…' : '<span class="arrow">⟳</span> Scan';
  $('currentMachineNav').textContent = machineName;
  $('currentMachineState').textContent = paired ? 'Companion connected' : 'Not connected';
  $('nextTitle').textContent = `${repos.filter(eligible).length} repositories can receive updates`;
  $('nextText').textContent =
    'Review the eligible branches and the reasons other repositories need attention. This version previews updates; it does not pull or push.';
  const route = parseRoute();
  const onDetail = route.view === 'repo' && !other;
  const onStats = route.view === 'stats' && !other;
  $('overviewView').hidden = onDetail || onStats;
  $('detailView').hidden = !onDetail;
  $('statsView').hidden = !onStats;
  document.querySelectorAll('.nav button').forEach((button) => {
    button.classList.toggle(
      'active',
      (button.id === 'overviewNav' && route.view === 'overview') ||
        (button.id === 'statsNav' && route.view === 'stats'),
    );
  });
  if (onDetail) {
    detailPath = route.path;
    paintDetail();
    $('crumb').innerHTML = `Theana <span style="padding:0 10px">/</span> <a href="#/" style="color:inherit">Repositories</a> <span style="padding:0 10px">/</span> <b>${esc(
      (repos.find((r) => r.path === detailPath) || statsByPath[detailPath] || { name: detailPath.split(/[\\/]/).pop() }).name,
    )}</b>`;
  } else if (onStats) {
    paintStats();
    $('crumb').innerHTML =
      'Theana <span style="padding:0 10px">/</span> <a href="#/" style="color:inherit">Repositories</a> <span style="padding:0 10px">/</span> <b>Stats</b>';
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
    detailCursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + delta * 7 * 12);
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
    statsCursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + delta * 7 * 12);
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
function calendarGrid(map, series) {
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
      cells.push(/* HTML */ `
        <div
          class="cal-cell ${commits ? 'active' : 'zero'} ${commits && commits === peak ? 'peak' : ''}"
          title="${esc(formatDate(dateKey(date) + 'T12:00:00'))}: ${esc(commits)}"
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
          return /* HTML */ `
            <div
              class="cal-cell ${commits ? 'active' : 'zero'} ${commits && commits === peak ? 'peak' : ''}"
              title="${esc(label)}: ${esc(commits)}"
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
function timelineChart(map, series, mode, extraMeta = '') {
  if (!series.length) {
    return '<p class="note">No commit timeline in cache yet. Load or refresh portfolio stats.</p>';
  }
  const counts = series.map((item) => item.commits || 0);
  const max = Math.max(1, ...counts);
  const total = counts.reduce((sum, n) => sum + n, 0);
  const active = counts.filter((n) => n > 0).length;
  const peak = Math.max(...counts);
  const mid = Math.round(max / 2);
  const bars = series
    .map((item, index) => {
      const commits = item.commits || 0;
      const height = commits ? Math.max(8, Math.round((commits / max) * 170)) : 2;
      const label = axisLabel(item, index, series.length, mode);
      const tip = `${periodLabel(item.period, item.start, mode)} · ${commits} commit${commits === 1 ? '' : 's'}`;
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
  return /* HTML */ `
    <div class="timeline-panel">
      <div class="timeline-meta">
        <span>In view <strong>${esc(total)}</strong></span>
        <span>Active <strong>${esc(active)}/${esc(series.length)}</strong></span>
        <span>Peak <strong>${esc(peak)}</strong></span>
        ${extraMeta}
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
  return /* HTML */ `
    ${timelineChart(map, series, detailPeriod)}
    <div class="cal-nav">
      <button class="btn" id="calPrev">← Prev</button>
      <h3>${esc(calendarTitle(cursor, detailPeriod))}</h3>
      <button class="btn" id="calNext">Next →</button>
    </div>
    ${calendarGrid(map, series)}
  `;
}
function mergedCommitsByDate() {
  const map = {};
  paths.forEach((path) => {
    const byDate = statsByPath[path]?.commitsByDate || {};
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
  return paths
    .map((path) => {
      const s = statsByPath[path];
      const map = s?.commitsByDate || {};
      const commits = series.reduce((sum, item) => sum + commitsInBucket(map, item, mode), 0);
      return {
        path,
        name: s?.name || path.split(/[\\/]/).pop(),
        commits,
      };
    })
    .filter((row) => row.commits > 0)
    .sort((a, b) => b.commits - a.commits || a.name.localeCompare(b.name))
    .slice(0, limit);
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
  const rows = boardRows().sort(compareBoard);
  const hasCache = pulse.cached > 0;
  const coverage = `<span>Coverage <strong>${esc(pulse.cached)}/${esc(pulse.tracked)}</strong></span>`;
  $('statsView').innerHTML = /* HTML */ `
    <div class="heading">
      <div>
        <h1>Stats across repositories</h1>
        <p>Combined activity and a side-by-side comparison of tracked repos on this machine.</p>
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
    ${
      !paired
        ? '<div class="callout">Connect the companion to load local Git stats.</div>'
        : !paths.length
          ? '<div class="callout">Track repositories first, then Scan.</div>'
          : !hasCache
            ? '<div class="callout">No cached stats yet. Click Scan to refresh sync status and portfolio stats.</div>'
            : ''
    }
    <section class="stats-section">
      <div class="section-head">
        <h2>Activity</h2>
        <div class="period-tabs">
          ${['day', 'week', 'month']
            .map(
              (period) => /* HTML */ `
                <button data-stats-period="${period}" class="${statsPeriod === period ? 'selected' : ''}">
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
      ${
        tops.length
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
          : '<p class="note">No commits in this window across cached repositories.</p>'
      }
    </section>
    <section class="stats-section">
      <div class="section-head">
        <h2>Repository comparison</h2>
      </div>
      <div class="board-wrap">
        <table>
          <thead>
            <tr>
              <th class="sortable ${boardSortKey === 'name' ? (boardSortDir > 0 ? 'sorted-asc' : 'sorted-desc') : ''}" data-board-sort="name">Repo</th>
              <th class="sortable ${boardSortKey === 'commits7d' ? (boardSortDir > 0 ? 'sorted-asc' : 'sorted-desc') : ''}" data-board-sort="commits7d">7d</th>
              <th class="sortable ${boardSortKey === 'commits30d' ? (boardSortDir > 0 ? 'sorted-asc' : 'sorted-desc') : ''}" data-board-sort="commits30d">30d</th>
              <th class="sortable ${boardSortKey === 'commitCount' ? (boardSortDir > 0 ? 'sorted-asc' : 'sorted-desc') : ''}" data-board-sort="commitCount">All</th>
              <th class="sortable ${boardSortKey === 'lastCommitAt' ? (boardSortDir > 0 ? 'sorted-asc' : 'sorted-desc') : ''}" data-board-sort="lastCommitAt">Last commit</th>
              <th>30d spark</th>
            </tr>
          </thead>
          <tbody>
            ${
              rows.length
                ? rows
                    .map((row) => {
                      const num = (value) =>
                        value == null
                          ? '<td class="num muted">—</td>'
                          : `<td class="num">${esc(value)}</td>`;
                      return /* HTML */ `
                        <tr class="board-row" data-path="${esc(row.path)}" tabindex="0">
                          <td><strong>${esc(row.name)}</strong></td>
                          ${num(row.commits7d)}
                          ${num(row.commits30d)}
                          ${num(row.commitCount)}
                          <td class="${row.lastCommitAt ? '' : 'muted'}">
                            ${esc(row.lastCommitAt ? formatDate(row.lastCommitAt) : '—')}
                          </td>
                          <td>${row.hasCache ? sparkline(row.map) : '<span class="muted">—</span>'}</td>
                        </tr>
                      `;
                    })
                    .join('')
                : '<tr><td colspan="6" class="muted">No tracked repositories.</td></tr>'
            }
          </tbody>
        </table>
      </div>
    </section>
  `;
  bindStatsPage();
}
function bindStatsPage() {
  if ($('statsScan')) $('statsScan').onclick = runScan;
  document.querySelectorAll('[data-stats-period]').forEach((button) => {
    button.onclick = () => {
      statsPeriod = button.dataset.statsPeriod;
      statsCursor = null;
      paintStats();
    };
  });
  if ($('statsPrev')) {
    $('statsPrev').onclick = () => {
      shiftStatsCursor(-1);
      paintStats();
    };
  }
  if ($('statsNext')) {
    $('statsNext').onclick = () => {
      shiftStatsCursor(1);
      paintStats();
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
  const r =
    repos.find((repo) => repo.path === path) ||
    ({
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
    });
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
    ${
      s
        ? /* HTML */ `
            <section class="stats-section">
              <h2>Volume & age</h2>
              <div class="detail-grid">
                ${cells([
                  ['Total commits', s.commitCount ?? '—'],
                  ['Age', s.ageLabel || '—'],
                  ['First commit', formatDate(s.firstCommitAt)],
                  ['Lifespan', s.lifespanDays != null ? s.lifespanDays + ' days' : '—'],
                  ['7d', s.commits7d ?? '—'],
                  ['30d', s.commits30d ?? '—'],
                  ['90d', s.commits90d ?? '—'],
                  ['Tracked size', formatBytes(s.workingTreeBytes)],
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
                      (summary.peakDayPeriod ? ` · ${formatDate(summary.peakDayPeriod + 'T12:00:00')}` : ''),
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
                      <button data-period="${period}" class="${detailPeriod === period ? 'selected' : ''}">
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
              Stats computed ${esc(s.computedAt ? new Date(s.computedAt).toLocaleString() : '—')}
              · HEAD ${esc(s.head || '—')}
            </p>
            <button class="btn" id="refreshOneStats">Refresh stats</button>
          `
        : /* HTML */ `
            <div class="callout">Loading repository stats…</div>
            <button class="btn lime" id="refreshOneStats">Load stats</button>
          `
    }
    ${
      r.github
        ? /* HTML */ `
            <p style="margin-top:16px">
              <a href="${esc(r.github)}" target="_blank" rel="noopener">Open on GitHub ↗</a>
            </p>
          `
        : ''
    }
  `;
  $('backRepos').onclick = closeRepo;
  document.querySelectorAll('[data-period]').forEach((button) => {
    button.onclick = () => {
      detailPeriod = button.dataset.period;
      detailCursor = null;
      paintDetail();
    };
  });
  if ($('calPrev')) $('calPrev').onclick = () => {
    shiftCursor(-1);
    paintDetail();
  };
  if ($('calNext')) $('calNext').onclick = () => {
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
        mergeStats(result.repositories);
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
    cached.activitySummary
  ) {
    return;
  }
  try {
    const result = await api('stats', { path: detailPath });
    mergeStats(result.repositories);
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
        <span class="mono">--new-token</span>.
      </p>
      <label class="small" for="pairToken">Pairing token</label>
      <input class="setup-input" id="pairToken" type="password" autocomplete="off" value="${esc(token)}" />
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
      await pairWithToken(candidate);
      render();
      close();
      await hydrateStats();
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
      await hydrateStats();
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
    await hydrateStats();
  } catch {
    writeStoredToken('');
    token = '';
    paired = false;
  }
}
function manage() {
  if (!paired) return connect();
  const choices = new Map(
    paths.map((p) => [p, { path: p, name: p.split(/[\\/]/).pop(), checked: true }]),
  );
  let persistTimer = 0;
  open(
    /* HTML */ `
      <h3 id="dialogTitle">Manage tracked repos</h3>
      <p>
        Discover within an approved folder, then tick what to track on ${esc(machineName)}. Changes
        apply immediately.
      </p>
      <label class="small" for="rootPath">Discovery folder</label>
      <input class="setup-input" id="rootPath" value="${esc(roots[0] || '')}" />
      <div class="choice-toolbar">
        <button class="btn" id="discover">Discover repositories</button>
        <button class="btn" type="button" id="selectAll">Select all</button>
        <button class="btn" type="button" id="deselectAll">Deselect all</button>
      </div>
      <div id="choices" class="choice-list"></div>
      <label class="small" for="repoPath">Or add a repository by full path</label>
      <input class="setup-input" id="repoPath" placeholder="/path/to/a/repository" />
      <button class="btn" id="addPath">Add path</button>
      <p class="note">
        Approved folders: ${roots.map(esc).join(', ')}. Restart with another --root to approve an
        additional folder.
      </p>
      <p class="note" role="status" id="settingsError"></p>
    `,
    'Tracking settings',
  );
  const applySelection = async () => {
    try {
      const result = await api('selection', {
        paths: [...choices.values()].filter((r) => r.checked).map((r) => r.path),
      });
      applyStatus(result);
      statsByPath = Object.fromEntries(
        Object.entries(statsByPath).filter(([path]) => paths.includes(path)),
      );
      render();
      $('settingsError').textContent = `Tracking ${paths.length} repositor${
        paths.length === 1 ? 'y' : 'ies'
      }.`;
    } catch (e) {
      $('settingsError').textContent = e.message;
    }
  };
  const persist = () => {
    clearTimeout(persistTimer);
    persistTimer = setTimeout(applySelection, 120);
  };
  const draw = () => {
    $('choices').innerHTML =
      [...choices.values()]
        .map(
          (r, i) => /* HTML */ `
            <label class="repo-choice">
              <input type="checkbox" data-choice="${i}" ${r.checked ? 'checked' : ''} />
              <span>
                <strong>${esc(r.name)}</strong>
                <span class="small" style="display:block;margin-top:5px">${esc(r.path)}</span>
              </span>
            </label>
          `,
        )
        .join('') || '<p class="small">Discover a folder or add a path.</p>';
    document.querySelectorAll('[data-choice]').forEach((c) => {
      c.onchange = () => {
        [...choices.values()][Number(c.dataset.choice)].checked = c.checked;
        persist();
      };
    });
  };
  draw();
  $('selectAll').onclick = () => {
    choices.forEach((r) => {
      r.checked = true;
    });
    draw();
    persist();
  };
  $('deselectAll').onclick = () => {
    choices.forEach((r) => {
      r.checked = false;
    });
    draw();
    persist();
  };
  $('discover').onclick = async () => {
    $('discover').disabled = true;
    try {
      const result = await api('discover', { root: $('rootPath').value });
      result.repositories.forEach((r) => {
        if (!choices.has(r.path)) choices.set(r.path, { ...r, checked: false });
      });
      draw();
      $('settingsError').textContent =
        `Found ${result.repositories.length} repositories. Tick the ones you want.`;
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
        path,
        name: path.split(/[\\/]/).pop(),
        checked: true,
      });
      draw();
      $('repoPath').value = '';
      persist();
    }
  };
}
async function runScan() {
  if (!paired || busy || statsBusy || !paths.length) return;
  busy = true;
  statsBusy = true;
  render();
  try {
    const result = await api('scan', {});
    repos = result.repositories;
    machineName = result.machine;
    scannedAt = result.scannedAt;
    activity.unshift(
      `Scanned ${repos.length} repositories at ${new Date(scannedAt).toLocaleString()}`,
    );
    const stats = await api('stats', { all: true, refresh: true });
    mergeStats(stats.repositories);
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
$('machine').onchange = render;
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
  $('machine').value = 'current';
  if (parseRoute().view !== 'overview') location.hash = '#/';
  else render();
};
$('statsNav').onclick = () => openStats();
window.addEventListener('hashchange', () => {
  syncRoute();
});
$('machinesNav').onclick = () =>
  open(
    /* HTML */ `
      <h3 id="dialogTitle">Your machines</h3>
      <p>${esc(machineName)}: ${paired ? 'connected' : 'not connected'}</p>
      <p class="note">
        On your second computer, clone this project, start its companion, and pair this dashboard
        there. Selections are saved separately on each machine. Cross-machine report sharing is not
        enabled yet.
      </p>
    `,
    'Machines',
  );
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
