'use strict';
/* Pannello web dell'Alveare. Nessuna libreria esterna. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n, d = 2) => Number(n || 0).toLocaleString('it-IT', { maximumFractionDigits: d });

  // ------------------------------------------------ memoria locale (solo comodità)
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignora */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* ignora */ } },
  };
  let token = store.get('alveare_token');
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
    if (!res.ok) throw new Error(data.error || 'Qualcosa è andato storto');
    return data;
  }

  // ------------------------------------------------ moduli WebAssembly nel browser
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
    if ((await sha256hex(bytes)) !== m.sha256) throw new Error('Modulo non integro: ' + name);
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

  // ------------------------------------------------ IndexedDB: i testi inviati restano solo qui, per la prova del nove
  function idb() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open('alveare', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('jobs');
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  async function saveJobText(id, text) {
    try { const db = await idb(); db.transaction('jobs', 'readwrite').objectStore('jobs').put(text, String(id)); } catch { /* opzionale */ }
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

  // ------------------------------------------------ navigazione
  const VIEWS = ['landing', 'home', 'jobs', 'search', 'miele', 'privacy', 'expert'];
  function renderTabs(active) {
    const tabs = token
      ? [['home', 'Il mio alveare'], ['jobs', 'Chiedi un lavoro'], ['search', 'Cerca'], ['miele', 'Miele'], ['logout', 'Esci']]
      : [['landing', 'Inizio'], ['login', 'Entra']];
    $('#tabs').innerHTML = tabs.map(([k, l]) => `<button data-go="${k}" class="${k === active ? 'on' : ''}">${l}</button>`).join('');
  }
  async function go(view) {
    if (view === 'logout') return logout();
    if (view === 'login') return $('#dlg-login').showModal();
    if (view === 'home' && !token) view = 'landing';
    if (['home', 'jobs', 'search', 'miele', 'expert'].includes(view) && !token) view = 'landing';
    VIEWS.forEach((v) => $('#v-' + v).classList.toggle('hidden', v !== view));
    renderTabs(view);
    store.set('alveare_view', view);
    window.scrollTo(0, 0);
    if (view === 'landing') loadPublic();
    if (view === 'home') loadHome();
    if (view === 'jobs') loadJobs();
    if (view === 'miele') loadLedger();
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
    token = null; me = null; store.del('alveare_token');
    go('landing');
  }

  // ------------------------------------------------ registrazione e accesso
  function formHandler(id, path) {
    const f = $(id);
    f.addEventListener('submit', async (ev) => {
      if (ev.submitter && ev.submitter.value === 'cancel') return;
      ev.preventDefault();
      const d = Object.fromEntries(new FormData(f));
      try {
        const r = await api('POST', path, { username: d.username.trim(), password: d.password });
        token = r.token; store.set('alveare_token', token);
        f.closest('dialog').close(); f.reset();
        go('home');
      } catch (e) { toast(e.message); }
    });
  }
  formHandler('#f-register', '/api/auth/register');
  formHandler('#f-login', '/api/auth/login');

  // ------------------------------------------------ disegno dell'alveare
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
    return `<svg viewBox="0 0 ${W.toFixed(0)} ${H.toFixed(0)}" role="img" aria-label="Il favo della settimana">
      ${cells.map(([cx, cy], i) => `<polygon class="hex" points="${hex(cx, cy)}" fill="${i < filled ? '#f2a516' : '#fff3d6'}" stroke="#c97c06" stroke-width="2"/>`).join('')}
      <g class="flying"><use href="#ape" x="${W - 130}" y="4" width="110" height="92"/></g></svg>`;
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

  // ------------------------------------------------ il mio alveare
  const MEDAL_ICON = { primo_favo: '🍯', cento_cellette: '💯', sette_giorni: '📅', ape_notturna: '🌙', prima_richiesta: '✉️', memoria_condivisa: '♻️' };
  async function loadHome() {
    try {
      me = await api('GET', '/api/me');
      const s = await api('GET', '/api/public/stats');
      $('#me-name').textContent = me.username;
      $('#me-level').textContent = me.level.name;
      const lv = me.level;
      const prevAt = [0, 50, 200, 500, 1500, 5000, 15000][lv.index];
      $('#me-level-bar').style.width = lv.next_at ? Math.min(100, 100 * (me.earned_total - prevAt) / (lv.next_at - prevAt)) + '%' : '100%';
      $('#me-level-next').textContent = lv.next_at ? `Ancora ${fmt(lv.next_at - me.earned_total)} miele guadagnato per il prossimo livello` : 'Livello massimo raggiunto!';
      $('#me-miele').textContent = fmt(me.miele);
      $('#me-week').textContent = me.week.cells
        ? `Questa settimana la tua ape ha completato ${fmt(me.week.cells, 0)} cellette, elaborato ${fmt(me.week.records, 0)} testi e aiutato ${me.week.jobs_helped === 1 ? '1 lavoro' : fmt(me.week.jobs_helped, 0) + ' lavori'}.`
        : 'Questa settimana la tua ape non ha ancora lavorato.';
      $('#me-nodes').innerHTML = me.nodes.length ? me.nodes.map((n) => `
        <div class="row"><span><span class="bee-dot ${n.active ? 'on' : ''}"></span><b>${esc(n.name)}</b>
          <span class="muted small">${n.active ? 'si è fatta sentire da poco' : 'a riposo'} · ${fmt(n.accepted, 0)} cellette</span>
          ${n.banned ? '<span class="pill ko">sospesa</span>' : ''}</span>
          <button class="btn ghost small" data-node-del="${n.id}">Scollega</button></div>`).join('')
        : '<p class="muted">Non hai ancora un\'ape. Falla nascere qui sotto!</p>';
      $('#setup-cmd').textContent = `node alveare-node.js setup --server ${location.origin} --user ${me.username}`;
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
      box.innerHTML = `<p class="muted small">Lavora insieme ai tuoi amici e guardate il favo crescere. La classifica è solo tra voi.</p>
        <input type="text" id="g-name" placeholder="Nome del gruppo"> <p><button class="btn small" id="g-create">Crea gruppo</button></p>
        <input type="text" id="g-code" placeholder="Codice di un amico"> <p><button class="btn ghost small" id="g-join">Entra nel gruppo</button></p>`;
      $('#g-create').onclick = async () => { try { renderGroup(await api('POST', '/api/groups', { name: $('#g-name').value })); } catch (e) { toast(e.message); } };
      $('#g-join').onclick = async () => { try { renderGroup(await api('POST', '/api/groups/join', { code: $('#g-code').value })); } catch (e) { toast(e.message); } };
      return;
    }
    box.innerHTML = `<p><b>${esc(g.name)}</b> · codice da condividere: <code>${esc(g.code)}</code></p>
      <div class="list">${g.board.map((b, i) => `<div class="row"><span>${i + 1}. ${esc(b.username)}</span><span class="muted small">${fmt(b.cells_week, 0)} cellette · ${fmt(b.miele_week)} miele</span></div>`).join('') || '<p class="muted small">Nessuno in classifica.</p>'}</div>
      <label class="check"><input type="checkbox" id="g-vis" ${g.show_in_board ? 'checked' : ''}> <span class="small">Mostrami nella classifica del gruppo</span></label>
      <button class="btn ghost small" id="g-leave">Esci dal gruppo</button>`;
    $('#g-vis').onchange = (ev) => api('POST', '/api/groups/visibility', { show: ev.target.checked }).then(loadHome).catch((e) => toast(e.message));
    $('#g-leave').onclick = () => api('POST', '/api/groups/leave', {}).then(loadHome).catch((e) => toast(e.message));
  }

  document.addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-node-del]');
    if (!b) return;
    if (!confirm('Scollegare questa ape? Dovrai configurarla di nuovo per farla lavorare.')) return;
    try { await api('DELETE', '/api/nodes/' + b.dataset.nodeDel); loadHome(); } catch (e) { toast(e.message); }
  });

  // ------------------------------------------------ lavori
  function selectedOps() { return ['clean', ...($('#op-stats').checked ? ['stats'] : []), ...($('#op-embed').checked ? ['embed'] : [])]; }
  let estTimer = null;
  function scheduleEstimate() {
    clearTimeout(estTimer);
    estTimer = setTimeout(async () => {
      const text = $('#job-text').value;
      if (!text.trim()) { $('#job-estimate').textContent = 'Scrivi qualcosa per vedere quanto miele serve.'; return; }
      try {
        const e = await api('POST', '/api/jobs/estimate', { ops: selectedOps(), text });
        $('#job-estimate').innerHTML = `${fmt(e.records, 0)} testi · al massimo <b>${fmt(e.cost_miele_max, 0)} miele</b> (meno se l'alveare ha già parte dei risultati)`;
      } catch (err) { $('#job-estimate').textContent = err.message; }
    }, 400);
  }
  $('#job-text').addEventListener('input', scheduleEstimate);
  $('#op-stats').addEventListener('change', scheduleEstimate);
  $('#op-embed').addEventListener('change', scheduleEstimate);
  $('#job-file').addEventListener('change', async (ev) => {
    const f = ev.target.files[0];
    if (!f) return;
    if (f.size > 20 * 1024 * 1024) return toast('File troppo grande (massimo 20 MB)');
    $('#job-text').value = await f.text();
    if (!$('#job-name').value) $('#job-name').value = f.name.replace(/\.txt$/i, '');
    scheduleEstimate();
  });

  $('#job-send').addEventListener('click', async () => {
    const raw = $('#job-text').value;
    if (!raw.trim()) return toast('Non c\'è niente da inviare');
    const btn = $('#job-send'); btn.disabled = true;
    try {
      // pulizia dei dati personali QUI, nel tuo browser, prima dell'invio
      const cleaned = dec.decode(await runModule('pii_clean', enc.encode(raw)));
      const count = (s, t) => (s.split(t).length - 1);
      const removed = ['[EMAIL]', '[TELEFONO]', '[IBAN]', '[CF]'].map((t) => count(cleaned, t) - count(raw, t));
      const total = removed.reduce((a, b) => a + b, 0);
      if (total && !confirm(`Prima dell'invio abbiamo tolto: ${removed[0]} email, ${removed[1]} telefoni, ${removed[2]} IBAN, ${removed[3]} codici fiscali. Procedo?`)) return;
      const job = await api('POST', '/api/jobs', { name: $('#job-name').value || 'Lavoro', ops: selectedOps(), text: cleaned });
      await saveJobText(job.id, cleaned);
      $('#job-text').value = ''; $('#job-name').value = '';
      toast('Inviato! Le api sono al lavoro.');
      loadJobs();
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  });

  const STATUS = { phase1: ['Al lavoro', ''], phase2: ['Quasi pronto', ''], done: ['Pronto', 'ok'], failed: ['Non riuscito', 'ko'] };
  let jobsTimer = null;
  async function loadJobs() {
    clearTimeout(jobsTimer);
    try {
      const { jobs } = await api('GET', '/api/jobs');
      $('#jobs-list').innerHTML = jobs.length ? jobs.map((j) => {
        const [label, cls] = STATUS[j.status] || [j.status, ''];
        const pct = j.cells_total ? Math.round(100 * j.cells_done / j.cells_total) : 0;
        const st = j.stats && !j.stats.error ? `${fmt(j.stats.records, 0)} testi unici · ${fmt(j.stats.words, 0)} parole · ${fmt(j.stats.duplicates, 0)} doppioni` : (j.stats && j.stats.error) || '';
        return `<div class="card">
          <div class="row" style="background:none;border:0;padding:0"><b>${esc(j.name)}</b><span class="pill ${cls}">${label}</span></div>
          ${j.status !== 'done' && j.status !== 'failed' ? `<div class="progress" style="margin:10px 0"><i style="width:${pct}%"></i></div><p class="small muted">${j.cells_done} di ${j.cells_total} cellette</p>` : ''}
          <p class="small muted">${esc(st)} ${j.status === 'done' ? `· speso ${fmt(j.cost)} miele${j.reused ? ' · ♻️ riusati risultati già presenti' : ''}` : ''}</p>
          ${j.status === 'done' ? `<p>
            <a class="btn small" href="#" data-dl="${j.id}">Scarica risultati</a>
            <button class="btn ghost small" data-check="${j.id}">Prova del nove</button>
            ${j.memory_deleted ? '<span class="pill">rimosso dalla memoria</span>' : `<button class="btn ghost small" data-forget="${j.id}">Cancella dalla memoria</button>`}</p>
            <div id="check-${j.id}"></div>` : ''}
        </div>`;
      }).join('') : '<p class="muted">Nessun lavoro ancora. Prova a inviarne uno!</p>';
      if (jobs.some((j) => j.status === 'phase1' || j.status === 'phase2')) jobsTimer = setTimeout(loadJobs, 4000);
    } catch (e) { toast(e.message); }
  }

  document.addEventListener('click', async (ev) => {
    const dl = ev.target.closest('[data-dl]');
    if (dl) {
      ev.preventDefault();
      const res = await fetch(`/api/jobs/${dl.dataset.dl}/result`, { headers: { authorization: 'Bearer ' + token } });
      if (!res.ok) return toast('Download non riuscito');
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement('a'), { href: url, download: `lavoro-${dl.dataset.dl}.jsonl` });
      a.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    }
    const fg = ev.target.closest('[data-forget]');
    if (fg && confirm('Cancellare i risultati di questo lavoro dalla memoria dell\'alveare? I testi usati anche da altri lavori restano.')) {
      try { const r = await api('DELETE', `/api/jobs/${fg.dataset.forget}/memory`); toast(`Fatto: ${r.removed_items} testi rimossi`); loadJobs(); } catch (e) { toast(e.message); }
    }
    const ck = ev.target.closest('[data-check]');
    if (ck) proofOfNine(Number(ck.dataset.check));
    const ds = ev.target.closest('[data-dispute]');
    if (ds) openDispute(ds);
  });

  // ------------------------------------------------ prova del nove (rifatta nel tuo browser)
  async function proofOfNine(jobId) {
    const box = $('#check-' + jobId);
    box.innerHTML = '<p class="small muted">Rifaccio a campione il lavoro sul tuo PC...</p>';
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
      box.innerHTML = `<p class="small"><b>${ok} su ${sample.records.length}</b> controllati sono corretti ${bad.length ? '' : '✓'}
        ${recs ? '' : '<br><span class="muted">(i testi originali non sono su questo browser: controllata solo la mappa dei significati)</span>'}</p>` +
        bad.map((b) => `<div class="row"><span class="small">Testo n. ${b.idx + 1}: ${b.op === 'clean' ? 'pulizia' : 'mappa dei significati'} diversa</span>
          <button class="btn small" data-dispute="${jobId}" data-idx="${b.idx}" data-op="${b.op}" data-orig="${esc(b.original || '')}">Segnala</button></div>`).join('');
    } catch (e) { box.innerHTML = `<p class="small">${esc(e.message)}</p>`; }
  }

  async function openDispute(btn) {
    btn.disabled = true;
    try {
      const body = { op: btn.dataset.op, idx: Number(btn.dataset.idx) };
      if (body.op === 'clean') body.original = btn.dataset.orig;
      const { dispute_id } = await api('POST', `/api/jobs/${btn.dataset.dispute}/dispute`, body);
      btn.textContent = 'Due api stanno ricontrollando...';
      const poll = async () => {
        const d = await api('GET', '/api/disputes/' + dispute_id);
        if (d.status === 'open') return setTimeout(poll, 4000);
        btn.textContent = d.status === 'confirmed' ? 'Errore confermato e corretto' : 'Ricontrollato: ' + (d.outcome.result || '');
      };
      poll();
    } catch (e) { toast(e.message); btn.disabled = false; }
  }

  // ------------------------------------------------ cerca, miele, esperti
  async function doSearch() {
    const q = $('#search-q').value.trim();
    if (!q) return;
    try {
      const { results } = await api('GET', '/api/search?q=' + encodeURIComponent(q));
      $('#search-res').innerHTML = results.length ? results.map((r) => `<div class="row"><span>${esc(r.text)}</span></div>`).join('')
        : '<p class="muted">Nessun risultato. La memoria cresce con ogni lavoro!</p>';
    } catch (e) { toast(e.message); }
  }
  $('#search-go').addEventListener('click', doSearch);
  $('#search-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

  async function loadLedger() {
    try {
      const { entries } = await api('GET', '/api/me/ledger');
      $('#ledger-list').innerHTML = entries.map((e) => `<div class="row"><span>${esc(e.memo || e.kind)}<br><span class="small muted">${new Date(e.created * 1000).toLocaleString('it-IT')}</span></span>
        <b style="color:${e.amount >= 0 ? 'var(--verde)' : 'var(--rosso)'}">${e.amount >= 0 ? '+' : ''}${fmt(e.amount)}</b></div>`).join('') || '<p class="muted">Nessun movimento.</p>';
    } catch (e) { toast(e.message); }
  }

  async function loadExpert() {
    try {
      const m = await api('GET', '/api/me');
      const mods = await (await fetch('/api/modules')).json();
      $('#expert-box').textContent = JSON.stringify({ nodi: m.nodes, moduli: mods.modules.map(({ signature, ...x }) => x), operazioni: mods.ops }, null, 2);
    } catch (e) { toast(e.message); }
  }

  go(token ? (store.get('alveare_view') || 'home') : 'landing');
})();
