'use strict';
// L'ape: decide da sola quando lavorare, chiede cellette quando è libera,
// le esegue nella sandbox e consegna i risultati sigillati.
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const inspector = require('inspector');
const EventEmitter = require('events');

const { Api } = require('./api');
const { runPacket } = require('./sandbox');
const { unb64, verifyModule } = require('./crypto');
const { Monitor } = require('./monitor');
const config = require('./config');

const PAUSE_FACTOR = { 1: 4, 2: 2, 3: 1, 4: 0.3, 5: 0 };   // riposo dopo ogni celletta (x tempo di calcolo)
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
    this.status = 'avvio';
    this.reason = 'La tua ape si sta svegliando';
    this.lastError = null;
    this.running = false;
    this.backoffMs = 10000;
    this.nextAskAt = 0;
    this.avgMs = 200;
    this.statsFile = path.join(config.HOME, 'stats.json');
    this.stats = this.loadStats();
    this.packetsDir = config.dir('packets');
    this.resultsDir = config.dir('results');
    try { os.setPriority(process.pid, os.constants.priority.PRIORITY_LOW); } catch { /* non supportato */ }
  }

  loadStats() {
    try { return JSON.parse(fs.readFileSync(this.statsFile, 'utf8')); } catch {
      return { cells_total: 0, miele_total: 0, busy_ms_total: 0, days: {} };
    }
  }

  saveStats() {
    const keep = Object.keys(this.stats.days).sort().slice(-60);
    this.stats.days = Object.fromEntries(keep.map((k) => [k, this.stats.days[k]]));
    fs.writeFileSync(this.statsFile, JSON.stringify(this.stats), { mode: 0o600 });
  }

  day(d = today()) {
    if (!this.stats.days[d]) this.stats.days[d] = { cells: 0, miele: 0, busy_ms: 0, records: 0 };
    return this.stats.days[d];
  }

  energyEuro(busyMs) {
    const kwh = (busyMs / 3600000) * (this.cfg.energy.watts_per_core / 1000);
    return kwh * this.cfg.energy.euro_per_kwh;
  }

  saveConfig() { config.save(this.cfg); }

  // ------------------------------------------------------------ decisione locale
  decide(snap) {
    const c = this.cfg;
    if (debuggerAttached()) return ['bloccata', 'Rilevato un debugger: per sicurezza la tua ape non lavora'];
    if (c.manual_pause) return ['pausa', 'In pausa: riparte quando vuoi tu'];
    if (snap.battery.onBattery && !c.work_on_battery) return ['riposo', 'Sei a batteria: la tua ape aspetta la corrente'];
    const now = new Date();
    const dayIdx = (now.getDay() + 6) % 7; // lunedì = 0
    const inSchedule = !!(c.schedule[dayIdx] && c.schedule[dayIdx][now.getHours()]);
    const idleLong = snap.idleSeconds !== null && snap.idleSeconds > 600;
    if (!inSchedule && !(c.always_when_idle && idleLong)) return ['riposo', 'Fuori dagli orari che hai scelto'];
    if (c.pause_when_user_active) {
      if (snap.idleSeconds !== null && snap.idleSeconds < 120) return ['riposo', 'Stai usando il PC: la tua ape ti lascia spazio'];
      if (snap.othersLoad > 0.6) return ['riposo', 'Il PC è impegnato con altri programmi'];
    }
    return ['lavoro', 'La tua ape sta lavorando per l\'alveare'];
  }

  setStatus(status, reason) {
    if (status !== this.status) {
      const was = this.status;
      this.status = status; this.reason = reason;
      this.emit('status', status);
      if (was === 'lavoro' && status !== 'lavoro') this.releaseQueued().catch(() => {});
    } else {
      this.reason = reason;
    }
  }

  // ------------------------------------------------------------ moduli firmati
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
        if (!check.ok || check.sha !== m.sha256) throw new Error(`Firma del modulo ${m.name} non valida: rifiutato`);
        fs.writeFileSync(file, bytes);
      }
      new WebAssembly.Module(bytes); // controlla che sia WebAssembly valido
      this.modules.set(`${m.name}@${m.version}`, bytes);
    }
  }

  // ------------------------------------------------------------ ciclo principale
  async start() {
    this.running = true;
    while (this.running) {
      try {
        const hello = await this.api.post('/api/node/hello', { client: 'node-js', version: '1.0.0', os: process.platform });
        if (hello.sign_public_key !== this.cfg.sign_public_key || hello.seal_public_key !== this.cfg.seal_public_key) {
          throw new Error('Le chiavi del server sono cambiate: per sicurezza ripeti la configurazione del nodo');
        }
        this.limits = hello;
        await this.loadModules();
        break;
      } catch (e) {
        this.lastError = e.message;
        this.setStatus('offline', 'Non riesco a contattare l\'alveare, riprovo tra poco');
        if (/chiavi del server/.test(e.message)) { this.running = false; return; }
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
      if (this.status !== 'lavoro') { await sleep(1000); continue; }
      if (!this.queue.length) {
        if (Date.now() < this.nextAskAt) { await sleep(500); continue; }
        await this.askWork();
        continue;
      }
      const pkt = this.queue.shift();
      await this.processPacket(pkt);
    }
  }

  stop() { this.running = false; return this.releaseQueued(); }

  // impostazioni cambiate dal pannello: si applicano subito
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
      const res = await this.api.post('/api/node/work', { max_packets: n, lease_seconds: leaseS });
      this.lastError = null;
      if (!res.packets.length) {
        // il server suggerisce quando riprovare (più spesso se ci sono lavori in corso)
        const hint = Number(res.retry_after) > 0 ? res.retry_after * 1000 : this.backoffMs;
        this.nextAskAt = Date.now() + Math.min(hint, this.backoffMs);
        this.backoffMs = Math.min(120000, this.backoffMs * 2);
        this.reason = 'L\'alveare per ora non ha cellette: la tua ape aspetta';
        return;
      }
      this.backoffMs = 10000;
      for (const p of res.packets) {
        p.received_at = Date.now();
        // il pacchetto resta CIFRATO anche su disco
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
    let out;
    const t0 = Date.now();
    try {
      // la chiave arriva solo adesso, separata dal pacchetto
      const { key } = await this.api.post('/api/node/key', { task_id: p.task_id, lease_token: p.lease_token });
      out = await runPacket({
        wasm, key: unb64(key), nonce: unb64(p.nonce), ct: unb64(p.ct), aad: unb64(p.aad),
        serverPub: unb64(this.cfg.seal_public_key),
        timeoutMs: Math.max(5000, p.lease_seconds * 1000 - (Date.now() - p.received_at) - 2000),
      });
    } catch (e) {
      this.lastError = 'Celletta non completata: ' + e.message;
      fs.rmSync(pktFile, { force: true });
      await this.api.post('/api/node/release', { task_ids: [p.task_id] }).catch(() => {});
      return;
    }
    const ms = Date.now() - t0;
    this.avgMs = this.avgMs * 0.8 + ms * 0.2;
    const delivery = {
      task_id: p.task_id, lease_token: p.lease_token, idem_key: this.idemKey(p),
      sealed: out.sealed, result_sha256: out.sha, ms, units: p.units,
    };
    const resFile = path.join(this.resultsDir, `${p.task_id}.json`);
    fs.writeFileSync(resFile, JSON.stringify(delivery), { mode: 0o600 }); // già sigillato per il server
    fs.rmSync(pktFile, { force: true });
    await this.deliver(delivery, resFile);
    const pause = ms * PAUSE_FACTOR[Math.max(1, Math.min(5, Number(this.cfg.intensity) || 3))];
    if (pause > 0) await sleep(Math.min(pause, 60000));
  }

  async deliver(d, file) {
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const { ms, units, ...body } = d;
        const res = await this.api.post('/api/node/result', body);
        fs.rmSync(file, { force: true });
        if (res.status === 'accepted') {
          const day = this.day();
          day.cells += 1; day.miele += res.miele || 0; day.busy_ms += d.ms || 0;
          this.stats.cells_total += 1; this.stats.miele_total += res.miele || 0; this.stats.busy_ms_total += d.ms || 0;
          this.saveStats();
          this.emit('cell', res);
        } else if (res.status === 'rejected') {
          this.lastError = 'Celletta rifiutata: ' + (res.reason || '');
        }
        return;
      } catch (e) {
        this.lastError = 'Consegna rimandata: ' + e.message;
        if (e.status && e.status < 500 && e.status !== 429) { fs.rmSync(file, { force: true }); return; }
        await sleep(2000 * (attempt + 1));
      }
    }
    // resta nella cartella risultati: verrà riconsegnato (la chiave unica evita doppi conteggi)
  }

  async resendPendingResults() {
    for (const f of fs.readdirSync(this.resultsDir)) {
      const file = path.join(this.resultsDir, f);
      try { await this.deliver(JSON.parse(fs.readFileSync(file, 'utf8')), file); } catch { fs.rmSync(file, { force: true }); }
    }
    for (const f of fs.readdirSync(this.packetsDir)) fs.rmSync(path.join(this.packetsDir, f), { force: true });
  }

  // ------------------------------------------------------------ stato per il pannello locale
  state() {
    const d = this.day();
    const weekKeys = Object.keys(this.stats.days).sort().slice(-7);
    const weekBusy = weekKeys.reduce((s, k) => s + this.stats.days[k].busy_ms, 0);
    const weekMiele = weekKeys.reduce((s, k) => s + this.stats.days[k].miele, 0);
    const snap = this.monitor.snapshot();
    return {
      status: this.status, reason: this.reason, last_error: this.lastError,
      node_name: this.cfg.node_name, server: this.cfg.server,
      today: { cells: d.cells, miele: +d.miele.toFixed(2), euro: +this.energyEuro(d.busy_ms).toFixed(3) },
      week: { miele: +weekMiele.toFixed(2), euro: +this.energyEuro(weekBusy).toFixed(3) },
      total: { cells: this.stats.cells_total, miele: +this.stats.miele_total.toFixed(2),
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
    };
  }
}

module.exports = { Bee };
