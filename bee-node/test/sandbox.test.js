// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// The sandbox protects the PC of whoever lends computing power, even from a faulty or hostile module.
// The hostile modules live in test/hostile (source and build.sh): they are never signed or distributed.
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { aesGcmEncrypt } = require('../lib/crypto');
const { runPacket, wasmMemoryMax } = require('../lib/sandbox');

const HOSTILE = path.join(__dirname, 'hostile');
const MODULI = path.join(__dirname, '..', '..', 'orchestrator', 'modules');

function packet(wasmFile, extra = {}) {
  const key = crypto.randomBytes(32);
  const aad = Buffer.from('test');
  const { nonce, ct } = aesGcmEncrypt(key, Buffer.from('one\ntwo'), aad);
  const serverPub = crypto.generateKeyPairSync('x25519').publicKey.export({ type: 'spki', format: 'der' }).subarray(-32);
  return { wasm: fs.readFileSync(wasmFile), key, nonce, ct, aad, serverPub, ...extra };
}

// child processes of this test that are still alive (Linux and macOS)
function liveChildren() {
  try { return execFileSync('pgrep', ['-P', String(process.pid)], { encoding: 'utf8' }).trim().split('\n').filter(Boolean); } catch { return []; }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test('distributed modules declare a memory limit of at most 256 MB', () => {
  for (const f of fs.readdirSync(MODULI).filter((x) => x.endsWith('.wasm'))) {
    const max = wasmMemoryMax(fs.readFileSync(path.join(MODULI, f)));
    assert.ok(max !== null && max <= 256 * 1048576, `${f}: limit ${max}`);
  }
});

test('a module that never ends: stopped when time runs out, no process left running',
  { skip: process.platform === 'win32' && 'pgrep is not available on Windows' }, async () => {
    const t0 = Date.now();
    await assert.rejects(runPacket(packet(path.join(HOSTILE, 'loop.wasm'), { timeoutMs: 1000 })), /time limit exceeded/);
    assert.ok(Date.now() - t0 < 3000, 'it must stop right after the time limit');
    await sleep(300);
    assert.deepStrictEqual(liveChildren(), [], 'the sandbox must have been shut down');
  });

test('a module asking for endless memory: stops at its declared limit', async () => {
  const r = await runPacket(packet(path.join(HOSTILE, 'memory-capped.wasm'), { timeoutMs: 20000 }));
  assert.ok(r.t.wasm_mem_mb <= 16, `memory used ${r.t.wasm_mem_mb} MB`);
});

test('a module with no memory limit: rejected before it starts', async () => {
  await assert.rejects(runPacket(packet(path.join(HOSTILE, 'memory-uncapped.wasm'))), /memory limit/);
});

test('a module with too high a memory limit: rejected before it starts', async () => {
  await assert.rejects(runPacket(packet(path.join(HOSTILE, 'memory-huge.wasm'))), /4096 MB/);
});

test('a module asking for system functions: rejected', async () => {
  await assert.rejects(runPacket(packet(path.join(HOSTILE, 'imports.wasm'))), /imports functions/);
});

test('a file that is not WebAssembly: rejected', async () => {
  const p = packet(path.join(HOSTILE, 'loop.wasm'));
  p.wasm = Buffer.from('#!/bin/sh\nrm -rf ~\n');
  await assert.rejects(runPacket(p), /not a WebAssembly module/);
});

test('the sandbox is reused between cells (no needless start-ups) and restarted after an error', async () => {
  const { stopSandbox } = require('../lib/sandbox');
  stopSandbox();
  const text = path.join(MODULI, 'text_clean.wasm');
  const a = await runPacket(packet(text));
  const b = await runPacket(packet(text));
  assert.ok(a.startMs > 0, 'the first cell starts the sandbox');
  assert.strictEqual(b.startMs, 0, 'the second one reuses the running sandbox');
  assert.ok(b.t.compile_ms < a.t.compile_ms + 1, 'the module is not recompiled');
  await assert.rejects(runPacket(packet(path.join(HOSTILE, 'loop.wasm'), { timeoutMs: 500 })), /time limit exceeded/);
  const c = await runPacket(packet(text));
  assert.ok(c.startMs > 0, 'after a timeout the sandbox starts from scratch');
  stopSandbox();
});

test('parallel cells run one at a time, without mixing up results', async () => {
  const text = path.join(MODULI, 'text_clean.wasm');
  const out = await Promise.all([1, 2, 3, 4, 5].map(() => runPacket(packet(text))));
  assert.strictEqual(new Set(out.map((o) => o.id)).size, 5);
  for (const o of out) assert.strictEqual(o.records, 2);
  require('../lib/sandbox').stopSandbox();
});
