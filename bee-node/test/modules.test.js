// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// Tests for the WebAssembly modules and the node's cryptography: npm test
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { aesGcmDecrypt, aesGcmEncrypt, sealFor } = require('../lib/crypto');
const { runPacket } = require('../lib/sandbox');

const dir = path.join(__dirname, '..', '..', 'orchestrator', 'modules');
function run(name, text) {
  const e = new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(path.join(dir, name + '.wasm'))), {}).exports;
  e.reset();
  const input = Buffer.from(text);
  const p = e.alloc(input.length);
  new Uint8Array(e.memory.buffer).set(input, p);
  const o = e.run(p, input.length);
  return Buffer.from(new Uint8Array(e.memory.buffer, o, e.out_len()));
}

test('modules import nothing from the system', () => {
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.wasm'))) {
    const imports = WebAssembly.Module.imports(new WebAssembly.Module(fs.readFileSync(path.join(dir, f))));
    assert.deepStrictEqual(imports, [], f + ' has imports');
  }
});

test('cleaning: whitespace, capitals, accents, line count preserved', () => {
  const out = run('text_clean', '  Hello   WORLD\tÉCOLE Ça  va!\n\nLine 3  ').toString();
  assert.strictEqual(out, 'hello world école ça va!\n\nline 3');
});

test('personal data removed', () => {
  const out = run('pii_clean', 'mail a.b@c.it. phone +44 20 7946 0958, tax code RSSMRA85T10A562S, IBAN GB82 WEST 1234 5698 7654 32 ok').toString();
  assert.strictEqual(out, 'mail [EMAIL]. phone [PHONE], tax code [TAX_CODE], IBAN [IBAN] ok');
  assert.strictEqual(run('pii_clean', 'year 2024, version 1.2.3, 15 euros').toString(), 'year 2024, version 1.2.3, 15 euros');
});

test('statistics', () => {
  assert.strictEqual(run('text_stats', 'Hello world. How are you?\n\nOne line').toString(), '2 33 7 3');
});

test('meaning map: similar texts close, different texts far apart, normalised', () => {
  const b = run('embed_hash', 'the cat sleeps on the sofa\nthe cat sleeps on the couch\nadvanced quantum physics');
  const v = new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.length));
  const D = 256, dot = (i, j) => { let s = 0; for (let k = 0; k < D; k++) s += v[i * D + k] * v[j * D + k]; return s; };
  assert.ok(Math.abs(dot(0, 0) - 1) < 1e-5);
  assert.ok(dot(0, 1) > 0.6);
  assert.ok(dot(0, 2) < 0.2);
});

test('sandbox: decrypts, computes, seals; wrong key rejected', async () => {
  const server = crypto.generateKeyPairSync('x25519');
  const serverPub = Buffer.from(server.publicKey.export({ format: 'jwk' }).x, 'base64url');
  const key = crypto.randomBytes(32);
  const aad = Buffer.from('{"t":1}');
  const { nonce, ct } = aesGcmEncrypt(key, Buffer.from('Hello  WORLD'), aad);
  const wasm = fs.readFileSync(path.join(dir, 'text_clean.wasm'));
  const r = await runPacket({ wasm, key, nonce, ct, aad, serverPub });
  // opening on the server side
  const epk = Buffer.from(r.sealed.epk, 'base64');
  const shared = crypto.diffieHellman({ privateKey: server.privateKey,
    publicKey: crypto.createPublicKey({ key: { kty: 'OKP', crv: 'X25519', x: epk.toString('base64url') }, format: 'jwk' }) });
  const k2 = Buffer.from(crypto.hkdfSync('sha256', shared, epk, Buffer.from('hivenet-result-v1'), 32));
  const out = aesGcmDecrypt(k2, Buffer.from(r.sealed.nonce, 'base64'), Buffer.from(r.sealed.ct, 'base64'), aad);
  assert.strictEqual(out.toString(), 'hello world');
  await assert.rejects(runPacket({ wasm, key: crypto.randomBytes(32), nonce, ct, aad, serverPub }));
  await assert.rejects(runPacket({ wasm, key, nonce, ct, aad: Buffer.from('altro'), serverPub }));
  assert.ok(sealFor(serverPub, Buffer.from('x'), aad).epk);
});
