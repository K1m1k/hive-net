// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// The bee's performance metrics (lib/perf.js) and sandbox timings.
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { Perf } = require('../lib/perf');
const { aesGcmEncrypt } = require('../lib/crypto');
const { runPacket } = require('../lib/sandbox');

// test files stay inside the project (never in the system /tmp directory) and are deleted afterwards
const TEST_RUNS = path.join(__dirname, '..', '..', '.test-runs');
test.after(() => { for (const d of created) fs.rmSync(d, { recursive: true, force: true }); });
const created = [];
function tmpFile() {
  fs.mkdirSync(TEST_RUNS, { recursive: true });
  const d = fs.mkdtempSync(path.join(TEST_RUNS, 'perf-'));
  created.push(d);
  return path.join(d, 'perf.json');
}
function cell(extra = {}) {
  return { at: Date.now(), op: 'clean', records: 100, download_ms: 10, wait_ms: 500, key_ms: 5, sandbox_ms: 20,
    decrypt_ms: 1, compile_ms: 2, wasm_ms: 50, seal_ms: 2, upload_ms: 10, cpu_ms: 60, cell_ms: 100,
    down_bytes: 2048, up_bytes: 1024, rss_mb: 80, wasm_mem_mb: 1, ...extra };
}

test('with no cells the summary is empty and does not fail', () => {
  const p = new Perf(tmpFile());
  const s = p.summary();
  assert.strictEqual(s.cells, 0);
  assert.strictEqual(s.bottleneck, null);
  assert.match(p.report('test'), /No cells completed/);
});

test('queueing does not count as a cost and the shares add up to 100', () => {
  const p = new Perf(tmpFile());
  for (let i = 0; i < 10; i++) p.add(cell());
  const s = p.summary();
  assert.ok(!s.phases.some((x) => x.key === 'wait_ms'), 'queueing is not an active phase');
  assert.strictEqual(s.wait_avg_ms, 500);
  const tot = s.phases.reduce((a, x) => a + x.share, 0);
  assert.ok(Math.abs(tot - 100) < 0.5, `shares: ${tot}`);
  // 50 ms of computation out of 100 ms of active time
  assert.strictEqual(s.useful_share, 50);
  assert.strictEqual(s.bottleneck, 'Computation (healthy)');
});

test('bottleneck: the heaviest overhead phase', () => {
  const p = new Perf(tmpFile());
  for (let i = 0; i < 5; i++) p.add(cell({ sandbox_ms: 300, wasm_ms: 5 }));
  assert.strictEqual(p.summary().bottleneck, 'Sandbox start-up');
});

test('network speed and CPU computed from bytes and timings', () => {
  const p = new Perf(tmpFile());
  p.add(cell({ download_ms: 1000, down_bytes: 1024 * 100, upload_ms: 500, up_bytes: 1024 * 50, cpu_ms: 50, cell_ms: 100 }));
  const s = p.summary();
  assert.strictEqual(s.down_kbs, 100);
  assert.strictEqual(s.up_kbs, 100);
  assert.strictEqual(s.cpu_cores, 0.5);
});

test('metrics survive a restart and never grow beyond 500 cells', () => {
  const f = tmpFile();
  const p = new Perf(f);
  for (let i = 0; i < 520; i++) p.add(cell({ records: i }));
  const q = new Perf(f);
  assert.strictEqual(q.cells.length, 500);
  assert.strictEqual(q.cells[0].records, 20, 'the most recent ones are kept');
  assert.strictEqual((fs.statSync(f).mode & 0o777).toString(8), '600', 'file readable by its owner only');
});

test('the report contains the sections needed for an evaluation', () => {
  const p = new Perf(tmpFile());
  for (let i = 0; i < 3; i++) p.add(cell());
  const r = p.report('test bee');
  for (const t of ['Where the time goes', 'Useful work', 'Network and resources', 'Latest cells', 'test bee']) {
    assert.ok(r.includes(t), t);
  }
});

test('the sandbox times every phase and counts the texts', async () => {
  const wasm = fs.readFileSync(path.join(__dirname, '..', '..', 'orchestrator', 'modules', 'text_clean.wasm'));
  const key = crypto.randomBytes(32);
  const aad = Buffer.from('test');
  const { nonce, ct } = aesGcmEncrypt(key, Buffer.from('one\ntwo\nthree'), aad);
  const { publicKey } = crypto.generateKeyPairSync('x25519');
  const serverPub = publicKey.export({ type: 'spki', format: 'der' }).subarray(-32);
  const out = await runPacket({ wasm, key, nonce, ct, aad, serverPub });
  assert.strictEqual(out.records, 3);
  for (const k of ['decrypt_ms', 'compile_ms', 'wasm_ms', 'seal_ms']) {
    assert.ok(Number.isFinite(out.t[k]) && out.t[k] >= 0, k);
  }
  assert.ok(out.t.wasm_mem_mb > 0);
  assert.ok(out.t.cpu_ms >= 0 && out.t.rss_mb > 0, 'the sandbox reports its own CPU and memory');
});
