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
  statsByPath = {};
const base = 'http://127.0.0.1:48731';
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
async function computeStats(body, label) {
  if (!paired || statsBusy) return;
  statsBusy = true;
  render();
  try {
    const result = await api('stats', body);
    mergeStats(result.repositories);
    activity.unshift(label + ' at ' + new Date().toLocaleString());
    toast(label + ' complete for ' + (result.repositories || []).length + ' repositories.');
  } catch (e) {
    toast('Stats failed: ' + e.message);
  } finally {
    statsBusy = false;
    render();
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
    : repos.filter(
        (r) =>
          r.name.toLowerCase().includes($('search').value.toLowerCase()) &&
          (filter === 'all' ||
            (filter === 'behind' && r.behind > 0) ||
            (filter === 'ahead' && r.ahead > 0) ||
            (filter === 'dirty' && r.modified + r.untracked > 0)),
      );
  $('rows').innerHTML = list
    .map((r) => {
      const s = statsByPath[r.path];
      return /* HTML */ `
        <tr>
          <td>
            <span class="repo-name">${esc(r.name)}</span>
            <span class="repo-path">${esc(r.path)}</span>
          </td>
          <td><span class="branch">⑂ ${esc(r.branch || 'Unknown')}</span></td>
          <td>
            <span class="count ${s?.commitCount ? '' : 'zero'}">${s?.commitCount ?? '—'}</span>
          </td>
          <td>${esc(relativeTime(s?.lastCommitAt))}</td>
          <td>
            <span class="count ${s?.commits30d ? '' : 'zero'}">${s?.commits30d ?? '—'}</span>
          </td>
          <td>
            <span class="count ${r.behind ? 'behind' : 'zero'}">${r.behind ?? '?'}</span>
          </td>
          <td>
            <span class="count ${r.ahead ? 'ahead' : 'zero'}">${r.ahead ?? '?'}</span>
          </td>
          <td>
            ${r.modified + r.untracked
              ? `${r.modified + r.untracked} ${r.modified + r.untracked === 1 ? 'file' : 'files'}`
              : '✓ Clean'}
          </td>
          <td>
            <span class="badge ${eligible(r) || next(r) === 'Up to date' ? 'pull' : 'review'}">
              ${esc(next(r))}
            </span>
          </td>
          <td>
            <button class="row-btn" data-path="${esc(r.path)}">Stats ↗</button>
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
  $('portfolioActions').hidden = !showPortfolio;
  if (showPortfolio) {
    const hasCache = pulse.cached > 0;
    $('commitStat').textContent = hasCache ? String(pulse.commits) : '—';
    $('commit30Stat').textContent = hasCache ? String(pulse.commits30) : '—';
    $('contributorStat').textContent = hasCache ? String(pulse.contributors) : '—';
    $('statsCoverageStat').textContent = `${pulse.cached}/${pulse.tracked}`;
    $('statsCoverageLabel').textContent = hasCache
      ? 'Cached / tracked'
      : 'No stats loaded yet';
    $('statsMeta').textContent = statsBusy
      ? 'Computing stats…'
      : hasCache
        ? `Portfolio sums ${pulse.cached} cached repositories.`
        : 'Load stats to fill the portfolio row and table columns.';
    $('loadStats').disabled = statsBusy || busy || !paths.length;
    $('refreshStats').disabled = statsBusy || busy || !paths.length || !hasCache;
    $('loadStats').textContent = statsBusy ? 'Loading…' : 'Load portfolio stats';
    $('refreshStats').textContent = statsBusy ? 'Refreshing…' : 'Refresh all stats';
  }
  $('machineTitle').innerHTML = other
    ? 'Second machine <span>NOT SCANNED</span>'
    : esc(machineName) + ` <span>${paired ? 'CONNECTED' : 'NOT CONNECTED'}</span>`;
  $('machineMeta').textContent = other
    ? 'No report received'
    : paired
      ? `${paths.length} tracked repos · ${scannedAt ? 'Scanned ' + new Date(scannedAt).toLocaleString() : 'Not scanned yet'}`
      : 'Start your companion, then connect with its pairing token.';
  $('snapshotLabel').textContent =
    scannedAt && !other
      ? 'Compared with each branch’s upstream · ' + new Date(scannedAt).toLocaleString()
      : 'No live scan yet';
  $('currentInsights').hidden = other || !scannedAt;
  $('scan').disabled = other || !paired || busy || statsBusy || !paths.length;
  $('sync').disabled = other || !scannedAt || busy || statsBusy;
  $('manage').disabled = other || !paired || busy || statsBusy;
  $('connect').textContent = paired ? 'Connection settings' : 'Connect companion';
  $('scan').innerHTML = busy ? 'Scanning…' : '<span class="arrow">⟳</span> Scan this machine';
  $('currentMachineNav').textContent = machineName;
  $('currentMachineState').textContent = paired ? 'Companion connected' : 'Not connected';
  $('nextTitle').textContent = `${repos.filter(eligible).length} repositories can receive updates`;
  $('nextText').textContent =
    'Review the eligible branches and the reasons other repositories need attention. This version previews updates; it does not pull or push.';
  document
    .querySelectorAll('[data-path]')
    .forEach((b) => (b.onclick = () => details(repos.find((r) => r.path === b.dataset.path))));
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
function renderStatsPanel(r, s) {
  const contributors = (s?.topContributors || [])
    .map(
      (c) =>
        `<li><span>${esc(c.name)}</span><span>${esc(c.commits)}</span></li>`,
    )
    .join('');
  const languages = (s?.languages || [])
    .map(
      (l) =>
        `<li><span>${esc(l.extension)}</span><span>${esc(l.files)} · ${esc(formatBytes(l.bytes))}</span></li>`,
    )
    .join('');
  const dirs = (s?.largestDirs || [])
    .map(
      (d) =>
        `<li><span>${esc(d.name)}</span><span>${esc(formatBytes(d.bytes))}</span></li>`,
    )
    .join('');
  const remotes = (s?.remotes || [])
    .map((m) => `${esc(m.name)} · ${esc(m.url)}`)
    .join('<br>') || 'None';
  return /* HTML */ `
    <h3 id="dialogTitle">${esc(r.name)}</h3>
    <p>${esc(r.branch)} → ${esc(r.upstream || 'No upstream')} · ${esc(next(r))}</p>
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
            <div class="stats-section">
              <h2>History & activity</h2>
              <div class="detail-grid">
                ${cells([
                  ['Commits', s.commitCount ?? '—'],
                  ['30d', s.commits30d ?? '—'],
                  ['90d', s.commits90d ?? '—'],
                  ['Age', relativeTime(s.firstCommitAt)],
                ])}
              </div>
              <p class="note" style="margin-top:12px">
                Last: ${esc(s.lastCommitAuthor || '—')} · ${esc(relativeTime(s.lastCommitAt))}<br />
                ${esc(s.lastCommitSubject || '')}
              </p>
              ${sparkline(s.weeklyCommits)}
              <p class="small">Commits per week · last 12 weeks</p>
            </div>
            <div class="stats-section">
              <h2>Contributors</h2>
              <p class="small">${esc(s.contributorCount ?? '—')} total · top 5</p>
              <ul class="contrib-list">
                ${contributors || '<li><span>None</span></li>'}
              </ul>
            </div>
            <div class="stats-section">
              <h2>Shape</h2>
              <div class="detail-grid">
                ${cells([
                  ['Tracked size', formatBytes(s.workingTreeBytes)],
                  ['Branches', s.branchCount ?? '—'],
                  ['Stashes', s.stashCount ?? '—'],
                  ['Files changed · 30d', s.filesChanged30d ?? '—'],
                ])}
              </div>
              <p class="small" style="margin-top:12px">Languages</p>
              <ul class="lang-list">${languages || '<li><span>None</span></li>'}</ul>
              <p class="small" style="margin-top:12px">Largest paths</p>
              <ul class="lang-list">${dirs || '<li><span>None</span></li>'}</ul>
            </div>
            <div class="stats-section">
              <h2>Remotes</h2>
              <div class="mono">${remotes}</div>
            </div>
            ${s.error ? `<div class="callout">${esc(s.error)}</div>` : ''}
            <p class="note">
              Stats computed ${esc(s.computedAt ? new Date(s.computedAt).toLocaleString() : '—')}
              · HEAD ${esc(s.head || '—')} · cache ${esc(s.ttlHours ?? 24)}h
            </p>
            <button class="btn" id="refreshOneStats">Refresh stats</button>
          `
        : /* HTML */ `
            <div class="callout">Stats are not loaded for this repository yet.</div>
            <button class="btn lime" id="refreshOneStats">Load stats</button>
          `
    }
    <div class="mono" style="margin-top:16px">${esc(r.path)}</div>
    ${
      r.github
        ? /* HTML */ `
            <a href="${esc(r.github)}" target="_blank" rel="noopener">Open on GitHub ↗</a>
          `
        : ''
    }
    <p class="note">
      Sync status is from the latest scan. Stats are local Git reads only and do not fetch remotes.
    </p>
  `;
}
async function details(r) {
  if (!r) return;
  open(renderStatsPanel(r, statsByPath[r.path]), 'Repository stats');
  const bindRefresh = () => {
    const button = $('refreshOneStats');
    if (!button) return;
    button.onclick = async () => {
      button.disabled = true;
      button.textContent = 'Computing…';
      try {
        const result = await api('stats', { path: r.path, refresh: !!statsByPath[r.path] });
        mergeStats(result.repositories);
        open(renderStatsPanel(r, statsByPath[r.path]), 'Repository stats');
        bindRefresh();
        render();
      } catch (e) {
        toast('Stats failed: ' + e.message);
        button.disabled = false;
        button.textContent = statsByPath[r.path] ? 'Refresh stats' : 'Load stats';
      }
    };
  };
  bindRefresh();
  if (!statsByPath[r.path]) {
    try {
      const result = await api('stats', { path: r.path });
      mergeStats(result.repositories);
      open(renderStatsPanel(r, statsByPath[r.path]), 'Repository stats');
      bindRefresh();
      render();
    } catch (e) {
      toast('Stats failed: ' + e.message);
    }
  }
}
function sync() {
  const ready = repos.filter(eligible),
    skipped = repos.filter((r) => !eligible(r));
  open(
    /* HTML */ `
      <h3 id="dialogTitle">Sync eligibility</h3>
      <p>Based on your latest scan. Each repository must be rechecked before any future update.</p>
      <div class="mono">
        ${ready.length
          ? ready.map((r) => `↓ ${esc(r.name)} · ${r.behind} commits`).join('<br><br>')
          : 'No branches eligible for a fast-forward update.'}
      </div>
      <h2>Require review</h2>
      <p style="margin-top:12px">
        ${skipped.map((r) => `${esc(r.name)}: ${esc(next(r))}`).join('<br>') || 'None'}
      </p>
      <div class="callout">
        Pulling and pushing are not enabled in this version. Scanning fetches remote references and
        reads status; your checked-out files are not changed.
      </div>
    `,
    'Sync preview',
  );
}
function connect() {
  open(
    /* HTML */ `
      <h3 id="dialogTitle">Connect this machine</h3>
      <p>Start the companion in your local repo checkout:</p>
      <div class="mono">python3 companion/server.py --root ~/Projects</div>
      <p>
        Paste the pairing token printed in your terminal. It changes when the companion restarts.
      </p>
      <label class="small" for="pairToken">Pairing token</label>
      <input class="setup-input" id="pairToken" type="password" autocomplete="off" />
      <button class="btn lime" id="pair">Connect</button>
      <p id="pairError" class="note" role="status"></p>
      <p class="note">
        The browser may ask to access localhost. The token stays in this tab’s memory. No login
        credentials or source files are sent to hosting.
      </p>
    `,
    'Connection settings',
  );
  $('pair').onclick = async () => {
    const candidate = $('pairToken').value.trim();
    if (!candidate) return;
    const previous = token;
    token = candidate;
    $('pair').disabled = true;
    try {
      const result = await api('status');
      paired = true;
      machineName = result.machine;
      roots = result.roots;
      paths = result.paths;
      repos = [];
      scannedAt = null;
      statsByPath = {};
      render();
      close();
      await hydrateStats();
      manage();
    } catch (e) {
      token = previous;
      $('pairError').textContent =
        'Could not connect: ' +
        e.message +
        ' Check the companion and browser localhost permission.';
      $('pair').disabled = false;
    }
  };
}
function manage() {
  if (!paired) return connect();
  const choices = new Map(
    paths.map((p) => [p, { path: p, name: p.split(/[\\/]/).pop(), checked: true }]),
  );
  open(
    /* HTML */ `
      <h3 id="dialogTitle">Manage tracked repos</h3>
      <p>
        Discover within an approved folder, then choose exactly what to track on
        ${esc(machineName)}.
      </p>
      <label class="small" for="rootPath">Discovery folder</label>
      <input class="setup-input" id="rootPath" value="${esc(roots[0] || '')}" />
      <button class="btn" id="discover">Discover repositories</button>
      <div id="choices" style="margin:20px 0;max-height:360px;overflow:auto"></div>
      <label class="small" for="repoPath">Or add a repository by full path</label>
      <input class="setup-input" id="repoPath" placeholder="/path/to/a/repository" />
      <button class="btn" id="addPath">Add path</button>
      <p class="note">
        Approved folders: ${roots.map(esc).join(', ')}. Restart with another --root to approve an
        additional folder.
      </p>
      <div style="margin-top:20px">
        <button class="btn lime" id="saveRepos">Save tracked repos</button>
      </div>
      <p class="note" role="status" id="settingsError"></p>
    `,
    'Tracking settings',
  );
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
    document
      .querySelectorAll('[data-choice]')
      .forEach(
        (c) =>
          (c.onchange = () =>
            ([...choices.values()][Number(c.dataset.choice)].checked = c.checked)),
      );
  };
  draw();
  $('discover').onclick = async () => {
    $('discover').disabled = true;
    try {
      const result = await api('discover', { root: $('rootPath').value });
      result.repositories.forEach((r) => {
        if (!choices.has(r.path)) choices.set(r.path, { ...r, checked: false });
      });
      draw();
      $('settingsError').textContent =
        `Found ${result.repositories.length} repositories. Select the ones you want.`;
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
    }
  };
  $('saveRepos').onclick = async () => {
    $('saveRepos').disabled = true;
    try {
      const result = await api('selection', {
        paths: [...choices.values()].filter((r) => r.checked).map((r) => r.path),
      });
      paths = result.paths;
      repos = [];
      scannedAt = null;
      statsByPath = Object.fromEntries(
        Object.entries(statsByPath).filter(([path]) => paths.includes(path)),
      );
      render();
      close();
      toast('Tracking selection saved on this machine. Click Scan to refresh.');
    } catch (e) {
      $('settingsError').textContent = e.message;
      $('saveRepos').disabled = false;
    }
  };
}
$('loadStats').onclick = () =>
  computeStats({ all: true }, 'Loaded portfolio stats');
$('refreshStats').onclick = () =>
  computeStats({ all: true, refresh: true }, 'Refreshed portfolio stats');
$('scan').onclick = async () => {
  busy = true;
  render();
  try {
    const result = await api('scan', {});
    repos = result.repositories;
    machineName = result.machine;
    scannedAt = result.scannedAt;
    activity.unshift(
      `Scanned ${repos.length} repositories at ${new Date(scannedAt).toLocaleString()}`,
    );
    toast(
      'Live scan complete. ' +
        repos.filter((r) => r.error).length +
        ' repositories need Git configuration or access checks.',
    );
  } catch (e) {
    toast('Scan failed: ' + e.message);
  } finally {
    busy = false;
    render();
  }
};
$('sync').onclick = sync;
$('review').onclick = sync;
$('connect').onclick = connect;
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
  render();
};
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
render();
