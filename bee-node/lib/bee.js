// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// The bee: decides on its own when to work, asks for cells when it is free,
// runs them in the sandbox and delivers the sealed results.
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const inspector = require('inspector');
const EventEmitter = require('events');

const { Api } = require('./api');
const { runPacket, stopSandbox } = require('./sandbox');
const { unb64, verifyModule } = require('./crypto');
const { Monitor } = require('./monitor');
const { Perf } = require('./perf');
const config = require('./config');

const PAUSE_FACTOR = { 1: 4, 2: 2, 3: 1, 4: 0.3, 5: 0 };   // rest after each cell (x computation time)
const PACKETS = { 1: 1, 2: 1, 3: 2, 4: 3, 5: 4 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const today = () => new Date().toISOString().slice(0, 10);

function debuggerAttached() {
  try { if (inspector.url()) return true; } catch { /* ignore */ }
  return process.execArgv.some((a) => a.startsWith('--inspect') || a.startsWith('--debug'));
}

class Bee extends EventEmitter {
  constructor(cfg, { log = console.log } = {}) {
    super();
    this.cfg = cfg;
    this.log = log;
    this.api = new Api(cfg.server, cfg.node_token);
    this.monitor = new Monitor();
    this.modules = new Map();
    this.queue = [];
    this.status = 'starting';
    this.reason = 'Your bee is waking up';
    this.lastError = null;
    this.running = false;
    this.backoffMs = 10000;
    this.nextAskAt = 0;
    this.avgMs = 200;
    this.statsFile = path.join(config.HOME, 'stats.json');
    this.stats = this.loadStats();
    this.perf = new Perf(path.join(config.HOME, 'perf.json'));
    this.lastUploadMs = 0;
    this.packetsDir = config.dir('packets');
    this.resultsDir = config.dir('results');
    try { os.setPriority(process.pid, os.constants.priority.PRIORITY_LOW); } catch { /* not supported */ }
  }

  loadStats() {
    try { return JSON.parse(fs.readFileSync(this.statsFile, 'utf8')); } catch {
      return { cells_total: 0, honey_total: 0, busy_ms_total: 0, days: {} };
    }
  }

  saveStats() {
    const keep = Object.keys(this.stats.days).sort().slice(-60);
    this.stats.days = Object.fromEntries(keep.map((k) => [k, this.stats.days[k]]));
    fs.writeFileSync(this.statsFile, JSON.stringify(this.stats), { mode: 0o600 });
  }

  day(d = today()) {
    if (!this.stats.days[d]) this.stats.days[d] = { cells: 0, honey: 0, busy_ms: 0, records: 0 };
    return this.stats.days[d];
  }

  energyEuro(busyMs) {
    const kwh = (busyMs / 3600000) * (this.cfg.energy.watts_per_core / 1000);
    return kwh * this.cfg.energy.euro_per_kwh;
  }

  saveConfig() { config.save(this.cfg); }

  // ------------------------------------------------------------ local decision
  decide(snap) {
    const c = this.cfg;
    if (debuggerAttached()) return ['blocked', 'A debugger was detected: for safety your bee will not work'];
    if (c.manual_pause) return ['paused', 'Paused: it will resume whenever you like'];
    if (snap.battery.onBattery && !c.work_on_battery) return ['resting', 'You are on battery: your bee is waiting for mains power'];
    const now = new Date();
    const dayIdx = (now.getDay() + 6) % 7; // Monday = 0
    const inSchedule = !!(c.schedule[dayIdx] && c.schedule[dayIdx][now.getHours()]);
    const idleLong = snap.idleSeconds !== null && snap.idleSeconds > 600;
    if (!inSchedule && !(c.always_when_idle && idleLong)) return ['resting', 'Outside the hours you chose'];
    if (c.pause_when_user_active) {
      if (snap.idleSeconds !== null && snap.idleSeconds < 120) return ['resting', 'You are using the PC: your bee is making room for you'];
      if (snap.othersLoad > 0.6) return ['resting', 'The PC is busy with other programs'];
    }
    return ['working', 'Your bee is working for the hive'];
  }

  setStatus(status, reason) {
    if (status !== this.status) {
      const was = this.status;
      this.status = status; this.reason = reason;
      this.emit('status', status);
      if (was === 'working' && status !== 'working') this.releaseQueued().catch(() => {});
    } else {
      this.reason = reason;
    }
  }

  // ------------------------------------------------------------ signed modules
  async loadModules() {
    const manifest = await this.api.get('/api/modules');
    const signPub = unb64(this.cfg.sign_public_key);
    const cacheDir = config.dir('modules');
    for (const m of manifest.modules) {
      const file = path.join(cacheDir, `${m.name}-${m.version}.wasm`);
      let bytes = fs.existsSync(file) ? fs.readFileSync(file) : null;
      let check = bytes && verifyModule(signPub, m.name, m.version, bytes, m.signature);
      if (!check || !check.ok || check.sha !== m.sha256) {
        bytes = await this.api.call('GET', `/modules/${m.name}.wasm`, undefined, { raw: true });
        check = verifyModule(signPub, m.name, m.version, bytes, m.signature);
        if (!check.ok || check.sha !== m.sha256) throw new Error(`Invalid signature for module ${m.name}: rejected`);
        fs.writeFileSync(file, bytes);
      }
      new WebAssembly.Module(bytes); // check that it is valid WebAssembly
      this.modules.set(`${m.name}@${m.version}`, bytes);
    }
  }

  // ------------------------------------------------------------ main loop
  async start() {
    this.running = true;
    while (this.running) {
      try {
        const hello = await this.api.post('/api/node/hello', { client: 'node-js', version: '1.0.0', os: process.platform });
        if (hello.sign_public_key !== this.cfg.sign_public_key || hello.seal_public_key !== this.cfg.seal_public_key) {
          throw new Error('The server keys have changed: for safety, set up the node again');
        }
        this.limits = hello;
        await this.loadModules();
        break;
      } catch (e) {
        this.lastError = e.message;
        this.setStatus('offline', 'Cannot reach the hive, retrying shortly');
        if (/server keys have changed/.test(e.message)) { this.running = false; return; }
        await sleep(15000);
      }
    }
    await this.resendPendingResults();
    this.lastSample = 0;
    while (this.running) {
      if (Date.now() - this.lastSample > 5000) {
        const snap = await this.monitor.sample();
        const [st, why] = this.decide(snap);
        this.setStatus(st, why);
        this.lastSample = Date.now();
      }
      if (this.status !== 'working') { await sleep(1000); continue; }
      if (!this.queue.length) {
        if (Date.now() < this.nextAskAt) { await sleep(500); continue; }
        await this.askWork();
        continue;
      }
      const pkt = this.queue.shift();
      await this.processPacket(pkt);
    }
  }

  stop() { this.running = false; stopSandbox(); return this.releaseQueued(); }

  // settings changed from the panel: applied immediately
  applySettings(patch) {
    const allowed = ['intensity', 'schedule', 'always_when_idle', 'pause_when_user_active', 'work_on_battery',
      'manual_pause', 'energy', 'node_name'];
    for (const k of allowed) if (k in patch) this.cfg[k] = patch[k];
    this.cfg.intensity = Math.max(1, Math.min(5, Number(this.cfg.intensity) || 3));
    this.saveConfig();
    const [st, why] = this.decide(this.monitor.snapshot());
    this.setStatus(st, why);
    this.lastSample = Date.now();
  }

  async askWork() {
    const lvl = Math.max(1, Math.min(5, Number(this.cfg.intensity) || 3));
    const n = PACKETS[lvl];
    const leaseS = Math.max(60, Math.ceil((n * this.avgMs * (1 + PAUSE_FACTOR[lvl])) / 1000) * 3 + 30);
    try {
      const tAsk = Date.now();
      const res = await this.api.post('/api/node/work', { max_packets: n, lease_seconds: leaseS });
      const askMs = Date.now() - tAsk;
      this.lastError = null;
      if (!res.packets.length) {
        // the server suggests when to retry (sooner when jobs are in progress)
        const hint = Number(res.retry_after) > 0 ? res.retry_after * 1000 : this.backoffMs;
        this.nextAskAt = Date.now() + Math.min(hint, this.backoffMs);
        this.backoffMs = Math.min(120000, this.backoffMs * 2);
        this.reason = 'The hive has no cells right now: your bee is waiting';
        return;
      }
      this.backoffMs = 10000;
      for (const p of res.packets) {
        p.received_at = Date.now();
        // request time split across the packets that arrived together
        p.m = { download_ms: askMs / res.packets.length, down_bytes: p.ct.length + p.aad.length + p.nonce.length };
        // the packet stays ENCRYPTED on disk as well
        fs.writeFileSync(path.join(this.packetsDir, `${p.task_id}.json`), JSON.stringify(p), { mode: 0o600 });
        this.queue.push(p);
      }
    } catch (e) {
      this.lastError = e.message;
      this.nextAskAt = Date.now() + this.backoffMs;
      this.backoffMs = Math.min(120000, this.backoffMs * 2);
    }
  }

  async releaseQueued() {
    const ids = this.queue.map((p) => p.task_id);
    this.queue = [];
    for (const id of ids) fs.rmSync(path.join(this.packetsDir, `${id}.json`), { force: true });
    if (ids.length) await this.api.post('/api/node/release', { task_ids: ids }).catch(() => {});
  }

  idemKey(p) { return crypto.createHash('sha256').update(`${p.task_id}:${p.lease_token}`).digest('hex'); }

  async processPacket(p) {
    const pktFile = path.join(this.packetsDir, `${p.task_id}.json`);
    const wasm = this.modules.get(`${p.module}@${p.version}`);
    if (!wasm || Date.now() > p.received_at + p.lease_seconds * 1000) {
      fs.rmSync(pktFile, { force: true });
      await this.api.post('/api/node/release', { task_ids: [p.task_id] }).catch(() => {});
      return;
    }
    let out, keyMs = 0, runMs = 0, cpuMs = 0;
    const t0 = Date.now();
    try {
      // the key only arrives now, separately from the packet
      const { key } = await this.api.post('/api/node/key', { task_id: p.task_id, lease_token: p.lease_token });
      keyMs = Date.now() - t0;
      const cpu0 = process.cpuUsage();
      const tRun = Date.now();
      out = await runPacket({
        wasm, key: unb64(key), nonce: unb64(p.nonce), ct: unb64(p.ct), aad: unb64(p.aad),
        serverPub: unb64(this.cfg.seal_public_key),
        timeoutMs: Math.max(5000, p.lease_seconds * 1000 - (Date.now() - p.received_at) - 2000),
      });
      runMs = Date.now() - tRun;
      const cpu = process.cpuUsage(cpu0);
      cpuMs = (cpu.user + cpu.system) / 1000;
    } catch (e) {
      this.lastError = 'Cell not completed: ' + e.message;
      fs.rmSync(pktFile, { force: true });
      await this.api.post('/api/node/release', { task_ids: [p.task_id] }).catch(() => {});
      return;
    }
    const ms = Date.now() - t0;
    this.avgMs = this.avgMs * 0.8 + ms * 0.2;
    const t = out.t || {};
    const inner = (t.decrypt_ms || 0) + (t.compile_ms || 0) + (t.wasm_ms || 0) + (t.seal_ms || 0);
    const m = {
      op: p.op, records: out.records || 0,
      download_ms: (p.m && p.m.download_ms) || 0, down_bytes: (p.m && p.m.down_bytes) || 0,
      wait_ms: Math.max(0, t0 - p.received_at), key_ms: keyMs,
      sandbox_ms: Math.max(0, runMs - inner), decrypt_ms: t.decrypt_ms || 0, compile_ms: t.compile_ms || 0,
      // bee process + sandbox process
      wasm_ms: t.wasm_ms || 0, seal_ms: t.seal_ms || 0, cpu_ms: cpuMs + (t.cpu_ms || 0),
      rss_mb: process.memoryUsage().rss / 1048576 + (t.rss_mb || 0), wasm_mem_mb: t.wasm_mem_mb || 0,
      prev_upload_ms: this.lastUploadMs, cores: os.cpus().length,
    };
    for (const k of Object.keys(m)) if (typeof m[k] === 'number') m[k] = Math.round(m[k] * 100) / 100;
    const delivery = {
      task_id: p.task_id, lease_token: p.lease_token, idem_key: this.idemKey(p),
      sealed: out.sealed, result_sha256: out.sha, ms, units: p.units, metrics: m,
    };
    const resFile = path.join(this.resultsDir, `${p.task_id}.json`);
    fs.writeFileSync(resFile, JSON.stringify(delivery), { mode: 0o600 }); // already sealed for the server
    fs.rmSync(pktFile, { force: true });
    await this.deliver(delivery, resFile);
    const pause = ms * PAUSE_FACTOR[Math.max(1, Math.min(5, Number(this.cfg.intensity) || 3))];
    if (pause > 0) await sleep(Math.min(pause, 60000));
  }

  async deliver(d, file) {
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const { ms, units, ...body } = d;
        const tUp = Date.now();
        const res = await this.api.post('/api/node/result', body);
        const upMs = Date.now() - tUp;
        this.lastUploadMs = upMs;
        fs.rmSync(file, { force: true });
        if (d.metrics) {
          const m = d.metrics;
          const upBytes = JSON.stringify(body).length;
          this.perf.add({ ...m, at: Date.now(), upload_ms: upMs, up_bytes: upBytes, status: res.status,
            cell_ms: Math.round(m.download_ms + m.wait_ms + m.key_ms + m.sandbox_ms + m.decrypt_ms + m.compile_ms + m.wasm_ms + m.seal_ms + upMs) });
        }
        if (res.status === 'accepted') {
          const day = this.day();
          day.cells += 1; day.honey += res.honey || 0; day.busy_ms += d.ms || 0;
          this.stats.cells_total += 1; this.stats.honey_total += res.honey || 0; this.stats.busy_ms_total += d.ms || 0;
          this.saveStats();
          this.emit('cell', res);
        } else if (res.status === 'rejected') {
          this.lastError = 'Cell rejected: ' + (res.reason || '');
        }
        return;
      } catch (e) {
        this.lastError = 'Delivery postponed: ' + e.message;
        if (e.status && e.status < 500 && e.status !== 429) { fs.rmSync(file, { force: true }); return; }
        await sleep(2000 * (attempt + 1));
      }
    }
    // stays in the results folder: it will be delivered again (the unique key prevents double counting)
  }

  async resendPendingResults() {
    for (const f of fs.readdirSync(this.resultsDir)) {
      const file = path.join(this.resultsDir, f);
      try { await this.deliver(JSON.parse(fs.readFileSync(file, 'utf8')), file); } catch { fs.rmSync(file, { force: true }); }
    }
    for (const f of fs.readdirSync(this.packetsDir)) fs.rmSync(path.join(this.packetsDir, f), { force: true });
  }

  // ------------------------------------------------------------ state for the local panel
  state() {
    const d = this.day();
    const weekKeys = Object.keys(this.stats.days).sort().slice(-7);
    const weekBusy = weekKeys.reduce((s, k) => s + this.stats.days[k].busy_ms, 0);
    const weekHoney = weekKeys.reduce((s, k) => s + this.stats.days[k].honey, 0);
    const snap = this.monitor.snapshot();
    return {
      status: this.status, reason: this.reason, last_error: this.lastError,
      node_name: this.cfg.node_name, server: this.cfg.server,
      today: { cells: d.cells, honey: +d.honey.toFixed(2), euro: +this.energyEuro(d.busy_ms).toFixed(3) },
      week: { honey: +weekHoney.toFixed(2), euro: +this.energyEuro(weekBusy).toFixed(3) },
      total: { cells: this.stats.cells_total, honey: +this.stats.honey_total.toFixed(2),
               euro: +this.energyEuro(this.stats.busy_ms_total).toFixed(3) },
      health: {
        others_load: Math.round(snap.othersLoad * 100),
        user_idle_s: snap.idleSeconds === null ? null : Math.round(snap.idleSeconds),
        battery: snap.battery,
      },
      settings: {
        intensity: this.cfg.intensity, schedule: this.cfg.schedule, always_when_idle: this.cfg.always_when_idle,
        pause_when_user_active: this.cfg.pause_when_user_active, work_on_battery: this.cfg.work_on_battery,
        manual_pause: this.cfg.manual_pause, energy: this.cfg.energy,
      },
      queue: this.queue.length,
      perf: this.perf.summary(),
    };
  }
}

module.exports = { Bee };
