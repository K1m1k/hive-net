'use strict';
// Test dei moduli WebAssembly e della crittografia del nodo: node --test test/
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { aesGcmDecrypt, aesGcmEncrypt, sealFor } = require('../lib/crypto');
const { runPacket } = require('../lib/sandbox');

const dir = path.join(__dirname, '..', '..', 'orchestratore', 'modules');
function run(name, text) {
  const e = new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(path.join(dir, name + '.wasm'))), {}).exports;
  e.reset();
  const input = Buffer.from(text);
  const p = e.alloc(input.length);
  new Uint8Array(e.memory.buffer).set(input, p);
  const o = e.run(p, input.length);
  return Buffer.from(new Uint8Array(e.memory.buffer, o, e.out_len()));
}

test('i moduli non importano nulla dal sistema', () => {
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.wasm'))) {
    const imports = WebAssembly.Module.imports(new WebAssembly.Module(fs.readFileSync(path.join(dir, f))));
    assert.deepStrictEqual(imports, [], f + ' ha importazioni');
  }
});

test('pulizia: spazi, maiuscole, accenti, numero di righe invariato', () => {
  const out = run('text_clean', '  Ciao   MONDO\tÀLBERO È  bello!\n\nRiga 3  ').toString();
  assert.strictEqual(out, 'ciao mondo àlbero è bello!\n\nriga 3');
});

test('dati personali rimossi', () => {
  const out = run('pii_clean', 'mail a.b@c.it. tel +39 333 123 4567, CF RSSMRA85T10A562S, IBAN IT60 X054 2811 1010 0000 0123 456 ok').toString();
  assert.strictEqual(out, 'mail [EMAIL]. tel [TELEFONO], CF [CF], IBAN [IBAN] ok');
  assert.strictEqual(run('pii_clean', 'anno 2024, versione 1.2.3, 15 euro').toString(), 'anno 2024, versione 1.2.3, 15 euro');
});

test('statistiche', () => {
  assert.strictEqual(run('text_stats', 'Ciao mondo. Come va?\n\nUna riga').toString(), '2 28 6 3');
});

test('mappa dei significati: testi simili vicini, diversi lontani, normalizzati', () => {
  const b = run('embed_hash', 'il gatto dorme sul divano\nil gatto dorme sul sofa\nfisica quantistica avanzata');
  const v = new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.length));
  const D = 256, dot = (i, j) => { let s = 0; for (let k = 0; k < D; k++) s += v[i * D + k] * v[j * D + k]; return s; };
  assert.ok(Math.abs(dot(0, 0) - 1) < 1e-5);
  assert.ok(dot(0, 1) > 0.6);
  assert.ok(dot(0, 2) < 0.2);
});

test('sandbox: decifra, calcola, sigilla; chiave sbagliata rifiutata', async () => {
  const server = crypto.generateKeyPairSync('x25519');
  const serverPub = Buffer.from(server.publicKey.export({ format: 'jwk' }).x, 'base64url');
  const key = crypto.randomBytes(32);
  const aad = Buffer.from('{"t":1}');
  const { nonce, ct } = aesGcmEncrypt(key, Buffer.from('Ciao  MONDO'), aad);
  const wasm = fs.readFileSync(path.join(dir, 'text_clean.wasm'));
  const r = await runPacket({ wasm, key, nonce, ct, aad, serverPub });
  // apertura lato server
  const epk = Buffer.from(r.sealed.epk, 'base64');
  const shared = crypto.diffieHellman({ privateKey: server.privateKey,
    publicKey: crypto.createPublicKey({ key: { kty: 'OKP', crv: 'X25519', x: epk.toString('base64url') }, format: 'jwk' }) });
  const k2 = Buffer.from(crypto.hkdfSync('sha256', shared, epk, Buffer.from('alveare-result-v1'), 32));
  const out = aesGcmDecrypt(k2, Buffer.from(r.sealed.nonce, 'base64'), Buffer.from(r.sealed.ct, 'base64'), aad);
  assert.strictEqual(out.toString(), 'ciao mondo');
  await assert.rejects(runPacket({ wasm, key: crypto.randomBytes(32), nonce, ct, aad, serverPub }));
  await assert.rejects(runPacket({ wasm, key, nonce, ct, aad: Buffer.from('altro'), serverPub }));
  assert.ok(sealFor(serverPub, Buffer.from('x'), aad).epk);
});
