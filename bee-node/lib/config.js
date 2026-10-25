// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// Node configuration, stored in ~/.hivenet (or in the HIVENET_NODE_HOME directory).
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME = process.env.HIVENET_NODE_HOME || path.join(os.homedir(), '.hivenet');

// Schedule: 7 days x 24 hours (true = the bee may work). Default: night time.
function nightSchedule() {
  return Array.from({ length: 7 }, () => Array.from({ length: 24 }, (_, h) => h >= 23 || h < 7));
}

const DEFAULTS = {
  server: '',
  node_id: null,
  node_token: '',
  node_name: 'My bee',
  seal_public_key: '',
  sign_public_key: '',
  intensity: 3,              // 1 tortoise ... 5 hare
  schedule: nightSchedule(),
  always_when_idle: false,   // also work outside the schedule if the PC has been idle for 10 minutes
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
