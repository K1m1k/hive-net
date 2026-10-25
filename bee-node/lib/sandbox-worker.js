// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// Runs in a separate process with limited memory. Here, and only here, the packet
// exists in plain text: it is decrypted, processed by the WebAssembly module (which
// has no access to files, network or screen), the result is sealed for the server
// and every plain-text buffer is overwritten before returning.
const { performance } = require('perf_hooks');
const { aesGcmDecrypt, sealFor, sha256hex } = require('./crypto');

function cpuOf(u) { return (u.user + u.system) / 1000; }

function wipe(...bufs) { for (const b of bufs) if (b && b.fill) b.fill(0); }

// modules already compiled, reused from one cell to the next (key: module hash)
const compiled = new Map();
function moduleFor(wasm) {
  const h = sha256hex(wasm);
  let m = compiled.get(h);
  if (!m) {
    m = new WebAssembly.Module(wasm);
    if (WebAssembly.Module.imports(m).length) throw new Error('the module imports functions from the system: rejected');
    if (compiled.size > 16) compiled.clear();
    compiled.set(h, m);
  }
  return m;
}

function work({ wasm, key, nonce, ct, aad, serverPub }) {
  const cpu0 = process.cpuUsage();
  const t0 = performance.now();
  const keyB = Buffer.from(key);
  const plain = aesGcmDecrypt(keyB, Buffer.from(nonce), Buffer.from(ct), Buffer.from(aad));
  wipe(keyB);
  const t1 = performance.now();
  let records = plain.length ? 1 : 0;
  for (let i = 0; i < plain.length; i++) if (plain[i] === 10) records++;
  // no imports: the module cannot call anything outside itself
  const inst = new WebAssembly.Instance(moduleFor(Buffer.from(wasm)), {});
  const e = inst.exports;
  if (e.abi_version() !== 1) throw new Error('unsupported module version');
  const tc = performance.now();
  e.reset();
  const p = e.alloc(plain.length);
  if (!p && plain.length) throw new Error('module out of memory');
  const mem = () => new Uint8Array(e.memory.buffer);
  mem().set(plain, p);
  wipe(plain);
  const op = e.run(p, plain.length);
  const len = e.out_len();
  const t2 = performance.now();
  const wasmMem = e.memory.buffer.byteLength;
  const out = Buffer.from(mem().subarray(op, op + len));
  mem().fill(0); // wipe the module's whole memory
  const sha = sha256hex(out);
  const sealed = sealFor(Buffer.from(serverPub), out, Buffer.from(aad));
  wipe(out);
  const t3 = performance.now();
  return { ok: true, sha, sealed, outLen: len, records,
    t: { decrypt_ms: t1 - t0, compile_ms: tc - t1, wasm_ms: t2 - tc, seal_ms: t3 - t2, wasm_mem_mb: wasmMem / 1048576,
      // CPU and memory of the sandbox process itself (the bee process measures only its own)
      cpu_ms: cpuOf(process.cpuUsage(cpu0)), rss_mb: process.memoryUsage().rss / 1048576 } };
}

// one packet at a time; every cell gets a fresh module instance (clean memory)
process.on('message', (data) => {
  let res;
  try { res = work(data); } catch (err) { res = { ok: false, error: String(err && err.message || err) }; }
  res.id = data.id;
  process.send(res);
});
// if the bee program exits, the sandbox exits with it
process.on('disconnect', () => process.exit(0));
