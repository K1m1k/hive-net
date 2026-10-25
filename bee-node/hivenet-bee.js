#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// HiveNet - bee (node) client.
//   node hivenet-bee.js setup --server https://hive-address --user name   (first time)
//   node hivenet-bee.js start                                              (start the bee)
//   node hivenet-bee.js status
const os = require('os');
const readline = require('readline');
const { Api } = require('./lib/api');
const { Bee } = require('./lib/bee');
const { startUi } = require('./lib/ui-server');
const config = require('./lib/config');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

function askHidden(question) {
  if (process.env.HIVENET_PASSWORD) return Promise.resolve(process.env.HIVENET_PASSWORD);
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    rl._writeToOutput = (s) => { if (s.startsWith(question)) rl.output.write(question); };
    rl.question(question, (a) => { rl.close(); process.stdout.write('\n'); resolve(a); });
  });
}

async function setup() {
  const server = arg('server');
  const user = arg('user');
  if (!server || !user) {
    console.log('Usage: node hivenet-bee.js setup --server https://hive-address --user your_name [--name "Bee name"]');
    process.exit(1);
  }
  const password = await askHidden('Password for ' + user + ': ');
  const pub = new Api(server);
  const { token } = await pub.post('/api/auth/login', { username: user, password });
  const api = new Api(server, token);
  const name = arg('name', `${os.hostname()}'s bee`);
  const reg = await api.post('/api/nodes', { name, os: `${process.platform} ${os.arch()}` });
  await api.post('/api/auth/logout', {}).catch(() => {});
  const cfg = config.load();
  Object.assign(cfg, {
    server, node_id: reg.node_id, node_token: reg.node_token, node_name: name,
    seal_public_key: reg.seal_public_key, sign_public_key: reg.sign_public_key,
  });
  config.save(cfg);
  console.log(`Done! Your bee "${cfg.node_name}" is ready. Start it with: node hivenet-bee.js start`);
}

async function start() {
  const cfg = config.load();
  if (!cfg.node_token) {
    console.log('Set up the node first: node hivenet-bee.js setup --server ... --user ...');
    process.exit(1);
  }
  const bee = new Bee(cfg);
  const port = Number(arg('ui-port', cfg.ui_port));
  try {
    await startUi(bee, port);
    console.log(`Your bee's panel: http://127.0.0.1:${port}`);
  } catch (e) {
    console.log(`Local panel not started (is port ${port} already in use?): ${e.message}`);
  }
  bee.on('status', (s) => console.log(new Date().toLocaleTimeString(), 'status:', s, '-', bee.reason));
  bee.on('cell', (r) => console.log(new Date().toLocaleTimeString(), `cell completed (+${r.honey} honey)`));
  const shutdown = async () => {
    console.log('\nYour bee is flying home: handing back the cells it has not started...');
    await Promise.race([bee.stop(), new Promise((r) => setTimeout(r, 5000))]);
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
  await bee.start();
}

function status() {
  const cfg = config.load();
  console.log(cfg.node_token ? `Bee "${cfg.node_name}" linked to ${cfg.server}` : 'Node not set up yet');
}

const cmd = process.argv[2];
const run = { setup, start, status }[cmd];
if (!run) {
  console.log('Commands: setup | start | status');
  process.exit(1);
}
Promise.resolve(run()).catch((e) => { console.error('Error:', e.message); process.exit(1); });
