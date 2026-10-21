'use strict';
// Configurazione del nodo, salvata in ~/.alveare (o nella cartella ALVEARE_NODE_HOME).
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME = process.env.ALVEARE_NODE_HOME || path.join(os.homedir(), '.alveare');

// Calendario: 7 giorni x 24 ore (true = l'ape può lavorare). Proposta: la notte.
function nightSchedule() {
  return Array.from({ length: 7 }, () => Array.from({ length: 24 }, (_, h) => h >= 23 || h < 7));
}

const DEFAULTS = {
  server: '',
  node_id: null,
  node_token: '',
  node_name: 'La mia ape',
  seal_public_key: '',
  sign_public_key: '',
  intensity: 3,              // 1 tartaruga ... 5 lepre
  schedule: nightSchedule(),
  always_when_idle: false,   // lavora anche fuori orario se il PC è inutilizzato da 10 minuti
  pause_when_user_active: true,
  work_on_battery: false,
  manual_pause: false,
  energy: { watts_per_core: 10, euro_per_kwh: 0.30 },
  ui_port: 7777,
};

function file() { return path.join(HOME, 'node.json'); }

function load() {
  try {
    const data = JSON.parse(fs.readFileSync(file(), 'utf8'));
    return { ...DEFAULTS, ...data, energy: { ...DEFAULTS.energy, ...(data.energy || {}) } };
  } catch {
    return { ...DEFAULTS };
  }
}

function save(cfg) {
  fs.mkdirSync(HOME, { recursive: true, mode: 0o700 });
  const tmp = file() + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cfg, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, file());
}

function dir(name) {
  const d = path.join(HOME, name);
  fs.mkdirSync(d, { recursive: true, mode: 0o700 });
  return d;
}

module.exports = { HOME, DEFAULTS, load, save, dir, nightSchedule };
