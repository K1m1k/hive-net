'use strict';
// NODO IMBROGLIONE, solo per i test: calcola davvero ma altera il risultato prima di
// sigillarlo. Serve a dimostrare che verifiche e prova del nove lo scoprono.
// Uso: node test/cheater.js <server> <node_token> <seal_pub_b64> <secondi>
const fs = require('fs');
const path = require('path');
const { Api } = require('../lib/api');
const { aesGcmDecrypt, sealFor, sha256hex, unb64 } = require('../lib/crypto');

const [server, token, sealPub, seconds] = process.argv.slice(2);
const api = new Api(server, token);
const modDir = path.join(__dirname, '..', '..', 'orchestratore', 'modules');
const until = Date.now() + Number(seconds || 20) * 1000;

function corrupt(op, out) {
  const b = Buffer.from(out);
  if (op === 'clean') { const i = b.indexOf(0x61); if (i >= 0) b[i] = 0x7a; else if (b.length) b[0] ^= 1; }
  else if (op === 'embed') { if (b.length >= 4) b.writeFloatLE(0.5, 0); }
  else if (op === 'stats') return Buffer.from('1 1 1 1');
  return b;
}

(async () => {
  let done = 0;
  while (Date.now() < until) {
    const { packets } = await api.post('/api/node/work', { max_packets: 2, lease_seconds: 60 });
    if (!packets.length) { await new Promise((r) => setTimeout(r, 300)); continue; }
    for (const p of packets) {
      const { key } = await api.post('/api/node/key', { task_id: p.task_id, lease_token: p.lease_token });
      const plain = aesGcmDecrypt(unb64(key), unb64(p.nonce), unb64(p.ct), unb64(p.aad));
      const e = new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(path.join(modDir, p.module + '.wasm'))), {}).exports;
      e.reset();
      const ptr = e.alloc(plain.length);
      new Uint8Array(e.memory.buffer).set(plain, ptr);
      const o = e.run(ptr, plain.length);
      const out = corrupt(p.op, Buffer.from(new Uint8Array(e.memory.buffer, o, e.out_len())));
      const res = await api.post('/api/node/result', {
        task_id: p.task_id, lease_token: p.lease_token, idem_key: 'cheat-' + p.task_id + '-' + p.lease_token,
        sealed: sealFor(unb64(sealPub), out, unb64(p.aad)), result_sha256: sha256hex(out),
      });
      if (res.status === 'accepted') done++;
    }
  }
  console.log(JSON.stringify({ cheated: done }));
})().catch((e) => { console.error(e); process.exit(1); });
