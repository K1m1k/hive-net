'use strict';
// Gira in un thread separato con memoria limitata. Qui, e solo qui, il pacchetto
// esiste in chiaro: viene decifrato, elaborato dal modulo WebAssembly (che non ha
// accesso a file, rete o schermo), il risultato viene sigillato per il server e
// tutti i buffer in chiaro vengono sovrascritti prima di uscire.
const { parentPort, workerData } = require('worker_threads');
const { aesGcmDecrypt, sealFor, sha256hex } = require('./crypto');

function wipe(...bufs) { for (const b of bufs) if (b && b.fill) b.fill(0); }

try {
  const { wasm, key, nonce, ct, aad, serverPub } = workerData;
  const keyB = Buffer.from(key);
  const plain = aesGcmDecrypt(keyB, Buffer.from(nonce), Buffer.from(ct), Buffer.from(aad));
  wipe(keyB);
  // nessuna importazione: il modulo non può chiamare nulla fuori da sé
  const inst = new WebAssembly.Instance(new WebAssembly.Module(wasm), {});
  const e = inst.exports;
  if (e.abi_version() !== 1) throw new Error('versione modulo non supportata');
  e.reset();
  const p = e.alloc(plain.length);
  if (!p && plain.length) throw new Error('memoria del modulo esaurita');
  const mem = () => new Uint8Array(e.memory.buffer);
  mem().set(plain, p);
  wipe(plain);
  const op = e.run(p, plain.length);
  const len = e.out_len();
  const out = Buffer.from(mem().subarray(op, op + len));
  mem().fill(0); // cancella tutta la memoria del modulo
  const sha = sha256hex(out);
  const sealed = sealFor(Buffer.from(serverPub), out, Buffer.from(aad));
  wipe(out);
  parentPort.postMessage({ ok: true, sha, sealed, outLen: len });
} catch (err) {
  parentPort.postMessage({ ok: false, error: String(err && err.message || err) });
}
