// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
/* HiveNet web panel. No external libraries. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n, d = 2) => Number(n || 0).toLocaleString('en-GB', { maximumFractionDigits: d });

  // ------------------------------------------------ local storage (convenience only)
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
  };
  let token = store.get('hivenet_token');
  let me = null;

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast'; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }

  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: { 'content-type': 'application/json', ...(token ? { authorization: 'Bearer ' + token } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && token) { logout(true); }
    if (!res.ok) throw new Error(data.error || 'Something went wrong');
    return data;
  }

  // ------------------------------------------------ WebAssembly modules in the browser
  const wasmCache = {};
  async function sha256hex(buf) {
    const h = await crypto.subtle.digest('SHA-256', buf);
    return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, '0')).join('');
  }
  async function loadModule(name) {
    if (wasmCache[name]) return wasmCache[name];
    const manifest = await (await fetch('/api/modules')).json();
    const m = manifest.modules.find((x) => x.name === name);
    const bytes = await (await fetch(`/modules/${name}.wasm`)).arrayBuffer();
    if ((await sha256hex(bytes)) !== m.sha256) throw new Error('Module integrity check failed: ' + name);
    wasmCache[name] = await WebAssembly.compile(bytes);
    return wasmCache[name];
  }
  async function runModule(name, inputBytes) {
    const inst = await WebAssembly.instantiate(await loadModule(name), {});
    const e = inst.exports;
    e.reset();
    const p = e.alloc(inputBytes.length);
    new Uint8Array(e.memory.buffer).set(inputBytes, p);
    const o = e.run(p, inputBytes.length);
    return new Uint8Array(e.memory.buffer).slice(o, o + e.out_len());
  }
  const enc = new TextEncoder(), dec = new TextDecoder();

  // ------------------------------------------------ IndexedDB: submitted texts are kept only here, for the spot check
  function idb() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open('hivenet', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('jobs');
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  async function saveJobText(id, text) {
    try { const db = await idb(); db.transaction('jobs', 'readwrite').objectStore('jobs').put(text, String(id)); } catch { /* optional */ }
  }
  async function loadJobText(id) {
    try {
      const db = await idb();
      return await new Promise((res) => { const r = db.transaction('jobs').objectStore('jobs').get(String(id)); r.onsuccess = () => res(r.result || null); r.onerror = () => res(null); });
    } catch { return null; }
  }
  function splitRecords(text) {
    text = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (text.endsWith('\n')) text = text.slice(0, -1);
    return text ? text.split('\n') : [];
  }

  // ------------------------------------------------ navigation
  const VIEWS = ['landing', 'home', 'jobs', 'search', 'honey', 'privacy', 'expert'];
  function renderTabs(active) {
    const tabs = token
      ? [['home', 'My hive'], ['jobs', 'Submit a job'], ['search', 'Search'], ['honey', 'Honey'], ['logout', 'Sign out']]
      : [['landing', 'Home'], ['login', 'Sign in']];
    $('#tabs').innerHTML = tabs.map(([k, l]) => `<button data-go="${k}" class="${k === active ? 'on' : ''}">${l}</button>`).join('');
  }
  async function go(view) {
    if (view === 'logout') return logout();
    if (view === 'login') return $('#dlg-login').showModal();
    if (view === 'home' && !token) view = 'landing';
    if (['home', 'jobs', 'search', 'honey', 'expert'].includes(view) && !token) view = 'landing';
    VIEWS.forEach((v) => $('#v-' + v).classList.toggle('hidden', v !== view));
    renderTabs(view);
    store.set('hivenet_view', view);
    window.scrollTo(0, 0);
    if (view === 'landing') loadPublic();
    if (view === 'home') loadHome();
    if (view === 'jobs') loadJobs();
    if (view === 'honey') loadLedger();
    if (view === 'expert') loadExpert();
  }
  document.addEventListener('click', (ev) => {
    const g = ev.target.closest('[data-go]');
    if (g) { ev.preventDefault(); go(g.dataset.go); }
    const o = ev.target.closest('[data-open]');
    if (o) { ev.preventDefault(); $('#' + o.dataset.open).showModal(); }
  });

  function logout(silent) {
    if (token && !silent) api('POST', '/api/auth/logout', {}).catch(() => {});
    token = null; me = null; store.del('hivenet_token');
    go('landing');
  }

  // ------------------------------------------------ sign-up and sign-in
  function formHandler(id, path) {
    const f = $(id);
    f.addEventListener('submit', async (ev) => {
      if (ev.submitter && ev.submitter.value === 'cancel') return;
      ev.preventDefault();
      const d = Object.fromEntries(new FormData(f));
      try {
        const r = await api('POST', path, { username: d.username.trim(), password: d.password });
        token = r.token; store.set('hivenet_token', token);
        f.closest('dialog').close(); f.reset();
        go('home');
      } catch (e) { toast(e.message); }
    });
  }
  formHandler('#f-register', '/api/auth/register');
  formHandler('#f-login', '/api/auth/login');

  // ------------------------------------------------ hive artwork
  function hiveArt(fraction) {
    const cols = 7, rows = 5, r = 26, w = Math.sqrt(3) * r;
    const cells = [];
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) cells.push([x * w + (y % 2) * w / 2 + w / 2 + 6, y * r * 1.5 + r + 6]);
    const filled = Math.round(cells.length * Math.min(1, fraction));
    const hex = (cx, cy) => Array.from({ length: 6 }, (_, i) => {
      const a = Math.PI / 180 * (60 * i - 30);
      return `${(cx + (r - 2) * Math.cos(a)).toFixed(1)},${(cy + (r - 2) * Math.sin(a)).toFixed(1)}`;
    }).join(' ');
    const W = cols * w + w / 2 + 12, H = rows * r * 1.5 + r / 2 + 12;
    return `<svg viewBox="0 0 ${W.toFixed(0)} ${H.toFixed(0)}" role="img" aria-label="This week's honeycomb">
      ${cells.map(([cx, cy], i) => `<polygon class="hex" points="${hex(cx, cy)}" fill="${i < filled ? '#f2a516' : '#fff3d6'}" stroke="#c97c06" stroke-width="2"/>`).join('')}
      <g class="flying"><use href="#bee" x="${W - 130}" y="4" width="110" height="92"/></g></svg>`;
  }

  async function loadPublic() {
    try {
      const s = await api('GET', '/api/public/stats');
      $('#pub-bees').textContent = fmt(s.active_bees, 0);
      $('#pub-today').textContent = fmt(s.cells_today, 0);
      $('#pub-week').textContent = fmt(s.cells_week, 0);
      $('#pub-goal').textContent = fmt(s.weekly_goal, 0);
      $('#pub-goal-bar').style.width = Math.min(100, 100 * s.cells_week / s.weekly_goal) + '%';
      $('#hive-art').innerHTML = hiveArt(s.cells_week / s.weekly_goal);
      return s;
    } catch { $('#hive-art').innerHTML = hiveArt(0.3); }
  }

  // ------------------------------------------------ my hive
  const MEDAL_ICON = { first_comb: '🍯', hundred_cells: '💯', seven_days: '📅', night_bee: '🌙', first_request: '✉️', shared_memory: '♻️' };
  async function loadHome() {
    try {
      me = await api('GET', '/api/me');
      const s = await api('GET', '/api/public/stats');
      $('#me-name').textContent = me.username;
      $('#me-level').textContent = me.level.name;
      const lv = me.level;
      const prevAt = [0, 50, 200, 500, 1500, 5000, 15000][lv.index];
      $('#me-level-bar').style.width = lv.next_at ? Math.min(100, 100 * (me.earned_total - prevAt) / (lv.next_at - prevAt)) + '%' : '100%';
      $('#me-level-next').textContent = lv.next_at ? `${fmt(lv.next_at - me.earned_total)} more honey earned to reach the next level` : 'Top level reached!';
      $('#me-honey').textContent = fmt(me.honey);
      $('#me-week').textContent = me.week.cells
        ? `This week your bee completed ${fmt(me.week.cells, 0)} ${me.week.cells === 1 ? 'cell' : 'cells'}, processed ${fmt(me.week.records, 0)} texts and helped ${me.week.jobs_helped === 1 ? '1 job' : fmt(me.week.jobs_helped, 0) + ' jobs'}.`
        : 'Your bee has not worked yet this week.';
      $('#me-nodes').innerHTML = me.nodes.length ? me.nodes.map((n) => `
        <div class="row"><span><span class="bee-dot ${n.active ? 'on' : ''}"></span><b>${esc(n.name)}</b>
          <span class="muted small">${n.active ? 'checked in recently' : 'resting'} · ${fmt(n.accepted, 0)} cells</span>
          ${n.banned ? '<span class="pill ko">suspended</span>' : ''}</span>
          <button class="btn ghost small" data-node-del="${n.id}">Unlink</button></div>`).join('')
        : '<p class="muted">You don\'t have a bee yet. Hatch one below!</p>';
      $('#setup-cmd').textContent = `node hivenet-bee.js setup --server ${location.origin} --user ${me.username}`;
      $('#me-medals').innerHTML = me.medals.map((m) => `<div class="medal ${m.earned ? 'on' : ''}" title="${esc(m.desc)}">
        <span class="ico">${MEDAL_ICON[m.id] || '🏅'}</span><b>${esc(m.name)}</b><br>${esc(m.desc)}</div>`).join('');
      $('#home-week').textContent = fmt(s.cells_week, 0);
      $('#home-goal').textContent = fmt(s.weekly_goal, 0);
      $('#home-goal-bar').style.width = Math.min(100, 100 * s.cells_week / s.weekly_goal) + '%';
      renderGroup(me.group);
    } catch (e) { toast(e.message); }
  }

  function renderGroup(g) {
    const box = $('#group-box');
    if (!g) {
      box.innerHTML = `<p class="muted small">Work together with your friends and watch your honeycomb grow. The leaderboard is just between you.</p>
        <input type="text" id="g-name" placeholder="Group name"> <p><button class="btn small" id="g-create">Create group</button></p>
        <input type="text" id="g-code" placeholder="A friend's code"> <p><button class="btn ghost small" id="g-join">Join group</button></p>`;
      $('#g-create').onclick = async () => { try { renderGroup(await api('POST', '/api/groups', { name: $('#g-name').value })); } catch (e) { toast(e.message); } };
      $('#g-join').onclick = async () => { try { renderGroup(await api('POST', '/api/groups/join', { code: $('#g-code').value })); } catch (e) { toast(e.message); } };
      return;
    }
    box.innerHTML = `<p><b>${esc(g.name)}</b> · code to share: <code>${esc(g.code)}</code></p>
      <div class="list">${g.board.map((b, i) => `<div class="row"><span>${i + 1}. ${esc(b.username)}</span><span class="muted small">${fmt(b.cells_week, 0)} cells · ${fmt(b.honey_week)} honey</span></div>`).join('') || '<p class="muted small">Nobody on the leaderboard yet.</p>'}</div>
      <label class="check"><input type="checkbox" id="g-vis" ${g.show_in_board ? 'checked' : ''}> <span class="small">Show me on the group leaderboard</span></label>
      <button class="btn ghost small" id="g-leave">Leave group</button>`;
    $('#g-vis').onchange = (ev) => api('POST', '/api/groups/visibility', { show: ev.target.checked }).then(loadHome).catch((e) => toast(e.message));
    $('#g-leave').onclick = () => api('POST', '/api/groups/leave', {}).then(loadHome).catch((e) => toast(e.message));
  }

  document.addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-node-del]');
    if (!b) return;
    if (!confirm('Unlink this bee? You will need to set it up again before it can work.')) return;
    try { await api('DELETE', '/api/nodes/' + b.dataset.nodeDel); loadHome(); } catch (e) { toast(e.message); }
  });

  // ------------------------------------------------ jobs
  function selectedOps() { return ['clean', ...($('#op-stats').checked ? ['stats'] : []), ...($('#op-embed').checked ? ['embed'] : [])]; }
  let estTimer = null;
  function scheduleEstimate() {
    clearTimeout(estTimer);
    estTimer = setTimeout(async () => {
      const text = $('#job-text').value;
      if (!text.trim()) { $('#job-estimate').textContent = 'Type something to see how much honey it takes.'; return; }
      try {
        const e = await api('POST', '/api/jobs/estimate', { ops: selectedOps(), text });
        $('#job-estimate').innerHTML = `${fmt(e.records, 0)} texts · at most <b>${fmt(e.cost_honey_max, 0)} honey</b> (less if the hive already has some of the results)`;
      } catch (err) { $('#job-estimate').textContent = err.message; }
    }, 400);
  }
  $('#job-text').addEventListener('input', scheduleEstimate);
  $('#op-stats').addEventListener('change', scheduleEstimate);
  $('#op-embed').addEventListener('change', scheduleEstimate);
  $('#job-file').addEventListener('change', async (ev) => {
    const f = ev.target.files[0];
    if (!f) return;
    if (f.size > 20 * 1024 * 1024) return toast('File too large (20 MB maximum)');
    $('#job-text').value = await f.text();
    if (!$('#job-name').value) $('#job-name').value = f.name.replace(/\.txt$/i, '');
    scheduleEstimate();
  });

  $('#job-send').addEventListener('click', async () => {
    const raw = $('#job-text').value;
    if (!raw.trim()) return toast('There is nothing to send');
    const btn = $('#job-send'); btn.disabled = true;
    try {
      // personal data is scrubbed HERE, in your browser, before sending
      const cleaned = dec.decode(await runModule('pii_clean', enc.encode(raw)));
      const count = (s, t) => (s.split(t).length - 1);
      const removed = ['[EMAIL]', '[PHONE]', '[IBAN]', '[TAX_CODE]'].map((t) => count(cleaned, t) - count(raw, t));
      const total = removed.reduce((a, b) => a + b, 0);
      if (total && !confirm(`Before sending we removed: ${removed[0]} email addresses, ${removed[1]} phone numbers, ${removed[2]} IBANs, ${removed[3]} tax codes. Continue?`)) return;
      const job = await api('POST', '/api/jobs', { name: $('#job-name').value || 'Job', ops: selectedOps(), text: cleaned });
      await saveJobText(job.id, cleaned);
      $('#job-text').value = ''; $('#job-name').value = '';
      toast('Sent! The bees are on it.');
      loadJobs();
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  });

  // ------------------------------------------------ time remaining and performance
  const etaSmooth = {}; // job id -> { eta, at }: moving average, as in download dialogs
  function smoothEta(id, eta) {
    if (eta === null || eta === undefined) { delete etaSmooth[id]; return null; }
    const prev = etaSmooth[id];
    const now = Date.now();
    let v = eta;
    if (prev) {
      const projected = Math.max(0, prev.eta - (now - prev.at) / 1000);
      v = projected * 0.6 + eta * 0.4;
    }
    etaSmooth[id] = { eta: v, at: now };
    return v;
  }
  function dur(s) {
    s = Math.max(0, Math.round(s));
    if (s < 60) return s + ' s';
    if (s < 3600) return Math.floor(s / 60) + ' min ' + (s % 60) + ' s';
    return Math.floor(s / 3600) + ' h ' + Math.floor((s % 3600) / 60) + ' min';
  }
  function liveLine(j) {
    const l = j.live || {};
    const eta = smoothEta(j.id, l.eta_s);
    const sp = l.speed || {};
    return `<div class="eta"><span>Time remaining: <b>${eta === null ? '—' : (eta < 5 ? 'a few seconds' : 'about ' + dur(eta))}</b>
        ${l.eta_note ? `<span class="muted small">(${esc(l.eta_note)})</span>` : ''}</span>
      <span class="muted">elapsed ${dur(l.elapsed_s || 0)}</span></div>
      <p class="small muted">${fmt(sp.bees || 0, 0)} ${sp.bees === 1 ? 'bee' : 'bees'} working · ${fmt(sp.cells_min || 0, 1)} cells/min · ${fmt(sp.records_s || 0, 1)} texts/s · ${fmt(sp.kb_s || 0, 1)} KB/s</p>`;
  }
  const PCOL = { server_queue_ms: '#b9a37e', download_ms: '#5b8def', wait_ms: '#d9c7a3', key_ms: '#9b6bd6', sandbox_ms: '#d68a3c', decrypt_ms: '#d6b23c', compile_ms: '#c97c06', wasm_ms: '#3f9b5a', seal_ms: '#8fbf5a', upload_ms: '#4fb3c9', server_ms: '#c4483e' };
  const perfHtml = {};
  const perfOpen = new Set(); // open panels stay open and refresh together with the list
  async function showPerf(id, refresh = false) {
    const box = $('#perf-' + id);
    if (!box) return;
    if (!refresh) {
      if (perfOpen.has(id)) { perfOpen.delete(id); box.innerHTML = ''; return; }
      perfOpen.add(id);
      box.innerHTML = '<p class="small muted">Collecting metrics...</p>';
    }
    try {
      const m = await api('GET', `/api/jobs/${id}/metrics`);
      if (!m.phases.some((p) => p.total_s > 0)) { box.innerHTML = '<div class="perf"><p class="small muted">No cells measured yet.</p></div>'; return; }
      const d = m.distribution, b = m.bottleneck;
      box.innerHTML = perfHtml[id] = `<div class="perf">
        <div class="tiles">
          <div><span class="small muted">Useful work</span><b>${fmt(m.useful_share, 1)}%</b><span class="small">of the time is computation</span></div>
          <div><span class="small muted">Estimated speed-up</span><b>${fmt(d.speedup_estimate, 2)}×</b><span class="small">versus a single PC</span></div>
          <div><span class="small muted">Bees used</span><b>${fmt(m.resources.bees, 0)}</b><span class="small">${fmt(d.utilization, 1)}% utilised</span></div>
          <div><span class="small muted">Data exchanged</span><b>${fmt(m.network.kb_in + m.network.kb_out, 1)} KB</b><span class="small">${fmt(m.network.kb_s, 1)} KB/s</span></div>
          <div><span class="small muted">Verification</span><b>${fmt(m.verification.share_of_compute, 1)}%</b><span class="small">of computation</span></div>
          <div><span class="small muted">Peak memory</span><b>${fmt(m.resources.rss_max_mb, 0)} MB</b><span class="small">on one bee</span></div>
        </div>
        <div class="bars">${m.phases.filter((p) => p.share > 0).map((p) => `<i style="width:${p.share}%;background:${PCOL[p.key]}" title="${esc(p.label)}: ${fmt(p.share, 1)}% · mean ${fmt(p.avg_ms, 1)} ms"></i>`).join('')}</div>
        <div class="legend">${m.phases.filter((p) => p.share > 0).map((p) => `<span style="--c:${PCOL[p.key]}">${esc(p.label)} ${fmt(p.share, 1)}%</span>`).join('')}</div>
        ${b ? `<p class="small"><b>Bottleneck: ${esc(b.label)}</b> (${fmt(b.share, 1)}%). ${esc(b.advice)}</p>` : ''}
        <p class="small muted">Queue waits: ${m.waits.map((w) => `${esc(w.label)}: ${fmt(w.avg_ms, 0)} ms on average`).join(' · ')}</p>
        <p><a class="btn small" href="#" data-file="${id}" data-kind="report">Download report</a>
           <a class="btn ghost small" href="#" data-file="${id}" data-kind="csv">Raw data (CSV)</a></p>
      </div>`;
    } catch (e) { box.innerHTML = ''; perfOpen.delete(id); toast(e.message); }
  }
  async function downloadFile(id, kind) {
    const path = kind === 'csv' ? `/api/jobs/${id}/metrics.csv` : `/api/jobs/${id}/report`;
    const name = kind === 'csv' ? `job-${id}-metrics.csv` : `job-${id}-report.md`;
    const res = await fetch(path, { headers: { authorization: 'Bearer ' + token } });
    if (!res.ok) return toast('Download failed');
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement('a'), { href: url, download: name });
    a.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  const STATUS = { phase1: ['In progress', ''], phase2: ['Almost done', ''], done: ['Ready', 'ok'], failed: ['Failed', 'ko'] };
  let jobsTimer = null;
  async function loadJobs() {
    clearTimeout(jobsTimer);
    try {
      const { jobs } = await api('GET', '/api/jobs');
      $('#jobs-list').innerHTML = jobs.length ? jobs.map((j) => {
        const [label, cls] = STATUS[j.status] || [j.status, ''];
        const pct = j.live && j.live.progress !== null && j.live.progress !== undefined ? Math.round(100 * j.live.progress)
          : (j.cells_total ? Math.round(100 * j.cells_done / j.cells_total) : 0);
        const st = j.stats && !j.stats.error ? `${fmt(j.stats.records, 0)} unique texts · ${fmt(j.stats.words, 0)} words · ${fmt(j.stats.duplicates, 0)} duplicates` : (j.stats && j.stats.error) || '';
        return `<div class="card">
          <div class="row" style="background:none;border:0;padding:0"><b>${esc(j.name)}</b><span class="pill ${cls}">${label}</span></div>
          ${j.status !== 'done' && j.status !== 'failed' ? `<div class="progress" style="margin:10px 0"><i style="width:${pct}%"></i></div><p class="small muted">${pct}% · ${j.cells_done} of ${j.cells_total} cells</p>${liveLine(j)}` : ''}
          <p class="small muted">${esc(st)} ${j.status === 'done' ? `· spent ${fmt(j.cost)} honey${j.reused ? ' · ♻️ reused existing results' : ''}` : ''}</p>
          ${j.status === 'done' ? `<p>
            <a class="btn small" href="#" data-dl="${j.id}">Download results</a>
            <button class="btn ghost small" data-check="${j.id}">Spot check</button>
            ${j.memory_deleted ? '<span class="pill">removed from memory</span>' : `<button class="btn ghost small" data-forget="${j.id}">Delete from memory</button>`}</p>
            <div id="check-${j.id}"></div>` : ''}
          <p><button class="btn ghost small" data-perf="${j.id}">Performance</button></p>
          <div id="perf-${j.id}">${perfOpen.has(j.id) ? (perfHtml[j.id] || '') : ''}</div>
        </div>`;
      }).join('') : '<p class="muted">No jobs yet. Try submitting one!</p>';
      for (const id of perfOpen) showPerf(id, true);
      if (jobs.some((j) => j.status === 'phase1' || j.status === 'phase2')) jobsTimer = setTimeout(loadJobs, 4000);
    } catch (e) { toast(e.message); }
  }

  document.addEventListener('click', async (ev) => {
    const dl = ev.target.closest('[data-dl]');
    if (dl) {
      ev.preventDefault();
      const res = await fetch(`/api/jobs/${dl.dataset.dl}/result`, { headers: { authorization: 'Bearer ' + token } });
      if (!res.ok) return toast('Download failed');
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement('a'), { href: url, download: `job-${dl.dataset.dl}.jsonl` });
      a.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    }
    const fg = ev.target.closest('[data-forget]');
    if (fg && confirm('Delete this job\'s results from the hive\'s memory? Texts also used by other jobs will be kept.')) {
      try { const r = await api('DELETE', `/api/jobs/${fg.dataset.forget}/memory`); toast(`Done: ${r.removed_items} texts removed`); loadJobs(); } catch (e) { toast(e.message); }
    }
    const pf = ev.target.closest('[data-perf]');
    if (pf) showPerf(Number(pf.dataset.perf));
    const fl = ev.target.closest('[data-file]');
    if (fl) { ev.preventDefault(); downloadFile(Number(fl.dataset.file), fl.dataset.kind); }
    const ck = ev.target.closest('[data-check]');
    if (ck) proofOfNine(Number(ck.dataset.check));
    const ds = ev.target.closest('[data-dispute]');
    if (ds) openDispute(ds);
  });

  // ------------------------------------------------ spot check (redone in your browser)
  async function proofOfNine(jobId) {
    const box = $('#check-' + jobId);
    box.innerHTML = '<p class="small muted">Redoing a sample of the work on your PC...</p>';
    try {
      const original = await loadJobText(jobId);
      const sample = await api('GET', `/api/jobs/${jobId}/sample?n=20`);
      const recs = original ? splitRecords(original) : null;
      let ok = 0; const bad = [];
      for (const s of sample.records) {
        if (recs) {
          const mine = dec.decode(await runModule('text_clean', enc.encode(recs[s.idx] || '')));
          if (mine !== s.clean) { bad.push({ idx: s.idx, op: 'clean', original: recs[s.idx] }); continue; }
        }
        if (s.embedding_f16) {
          const v = await runModule('embed_hash', enc.encode(s.clean));
          const f16 = F16.float32ToF16Bytes(new Float32Array(v.buffer, v.byteOffset, v.length / 4));
          const theirs = Uint8Array.from(atob(s.embedding_f16), (c) => c.charCodeAt(0));
          if (f16.length !== theirs.length || f16.some((b, i) => b !== theirs[i])) { bad.push({ idx: s.idx, op: 'embed' }); continue; }
        }
        ok++;
      }
      box.innerHTML = `<p class="small"><b>${ok} of ${sample.records.length}</b> checked are correct ${bad.length ? '' : '✓'}
        ${recs ? '' : '<br><span class="muted">(the original texts are not in this browser: only the meaning map was checked)</span>'}</p>` +
        bad.map((b) => `<div class="row"><span class="small">Text no. ${b.idx + 1}: ${b.op === 'clean' ? 'cleaning' : 'meaning map'} differs</span>
          <button class="btn small" data-dispute="${jobId}" data-idx="${b.idx}" data-op="${b.op}" data-orig="${esc(b.original || '')}">Report</button></div>`).join('');
    } catch (e) { box.innerHTML = `<p class="small">${esc(e.message)}</p>`; }
  }

  async function openDispute(btn) {
    btn.disabled = true;
    try {
      const body = { op: btn.dataset.op, idx: Number(btn.dataset.idx) };
      if (body.op === 'clean') body.original = btn.dataset.orig;
      const { dispute_id } = await api('POST', `/api/jobs/${btn.dataset.dispute}/dispute`, body);
      btn.textContent = 'Two bees are re-checking...';
      const poll = async () => {
        const d = await api('GET', '/api/disputes/' + dispute_id);
        if (d.status === 'open') return setTimeout(poll, 4000);
        btn.textContent = d.status === 'confirmed' ? 'Error confirmed and corrected' : 'Re-checked: ' + (d.outcome.result || '');
      };
      poll();
    } catch (e) { toast(e.message); btn.disabled = false; }
  }

  // ------------------------------------------------ search, honey, experts
  async function doSearch() {
    const q = $('#search-q').value.trim();
    if (!q) return;
    try {
      const { results } = await api('GET', '/api/search?q=' + encodeURIComponent(q));
      $('#search-res').innerHTML = results.length ? results.map((r) => `<div class="row"><span>${esc(r.text)}</span></div>`).join('')
        : '<p class="muted">No results. The memory grows with every job!</p>';
    } catch (e) { toast(e.message); }
  }
  $('#search-go').addEventListener('click', doSearch);
  $('#search-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

  async function loadLedger() {
    try {
      const { entries } = await api('GET', '/api/me/ledger');
      $('#ledger-list').innerHTML = entries.map((e) => `<div class="row"><span>${esc(e.memo || e.kind)}<br><span class="small muted">${new Date(e.created * 1000).toLocaleString('en-GB')}</span></span>
        <b style="color:${e.amount >= 0 ? 'var(--green)' : 'var(--red)'}">${e.amount >= 0 ? '+' : ''}${fmt(e.amount)}</b></div>`).join('') || '<p class="muted">No transactions.</p>';
    } catch (e) { toast(e.message); }
  }

  async function loadExpert() {
    try {
      const m = await api('GET', '/api/me');
      const mods = await (await fetch('/api/modules')).json();
      $('#expert-box').textContent = JSON.stringify({ nodes: m.nodes, modules: mods.modules.map(({ signature, ...x }) => x), operations: mods.ops }, null, 2);
    } catch (e) { toast(e.message); }
  }

  go(token ? (store.get('hivenet_view') || 'home') : 'landing');
})();
