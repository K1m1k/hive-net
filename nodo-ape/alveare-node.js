#!/usr/bin/env node
'use strict';
// Alveare - client del nodo.
//   node alveare-node.js setup --server https://indirizzo-alveare --user nome   (prima volta)
//   node alveare-node.js start                                                   (avvia l'ape)
//   node alveare-node.js status
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
  if (process.env.ALVEARE_PASSWORD) return Promise.resolve(process.env.ALVEARE_PASSWORD);
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
    console.log('Uso: node alveare-node.js setup --server https://indirizzo-alveare --user tuo_nome [--name "Nome ape"]');
    process.exit(1);
  }
  const password = await askHidden('Password di ' + user + ': ');
  const pub = new Api(server);
  const { token } = await pub.post('/api/auth/login', { username: user, password });
  const api = new Api(server, token);
  const reg = await api.post('/api/nodes', { name: arg('name', `Ape di ${os.hostname()}`), os: `${process.platform} ${os.arch()}` });
  await api.post('/api/auth/logout', {}).catch(() => {});
  const cfg = config.load();
  Object.assign(cfg, {
    server, node_id: reg.node_id, node_token: reg.node_token, node_name: arg('name', `Ape di ${os.hostname()}`),
    seal_public_key: reg.seal_public_key, sign_public_key: reg.sign_public_key,
  });
  config.save(cfg);
  console.log(`Fatto! La tua ape "${cfg.node_name}" è pronta. Avviala con: node alveare-node.js start`);
}

async function start() {
  const cfg = config.load();
  if (!cfg.node_token) {
    console.log('Prima configura il nodo: node alveare-node.js setup --server ... --user ...');
    process.exit(1);
  }
  const bee = new Bee(cfg);
  const port = Number(arg('ui-port', cfg.ui_port));
  try {
    await startUi(bee, port);
    console.log(`Pannello della tua ape: http://127.0.0.1:${port}`);
  } catch (e) {
    console.log(`Pannello locale non avviato (porta ${port} occupata?): ${e.message}`);
  }
  bee.on('status', (s) => console.log(new Date().toLocaleTimeString(), 'stato:', s, '-', bee.reason));
  bee.on('cell', (r) => console.log(new Date().toLocaleTimeString(), `celletta completata (+${r.miele} miele)`));
  const shutdown = async () => {
    console.log('\nLa tua ape torna all\'alveare: restituisco le cellette non iniziate...');
    await Promise.race([bee.stop(), new Promise((r) => setTimeout(r, 5000))]);
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
  await bee.start();
}

function status() {
  const cfg = config.load();
  console.log(cfg.node_token ? `Ape "${cfg.node_name}" collegata a ${cfg.server}` : 'Nodo non ancora configurato');
}

const cmd = process.argv[2];
const run = { setup, start, status }[cmd];
if (!run) {
  console.log('Comandi: setup | start | status');
  process.exit(1);
}
Promise.resolve(run()).catch((e) => { console.error('Errore:', e.message); process.exit(1); });
