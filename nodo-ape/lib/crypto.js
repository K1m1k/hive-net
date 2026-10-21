'use strict';
// Crittografia del nodo: decifra i pacchetti (AES-256-GCM), sigilla i risultati
// per il server (X25519 + HKDF + AES-256-GCM), verifica la firma dei moduli (Ed25519).
const crypto = require('crypto');

const SEAL_INFO = Buffer.from('alveare-result-v1');

function b64(buf) { return Buffer.from(buf).toString('base64'); }
function unb64(s) { return Buffer.from(s, 'base64'); }
function b64url(buf) { return Buffer.from(buf).toString('base64url'); }
function sha256hex(buf) { return crypto.createHash('sha256').update(buf).digest('hex'); }

function aesGcmDecrypt(key, nonce, ctWithTag, aad) {
  const tag = ctWithTag.subarray(ctWithTag.length - 16);
  const ct = ctWithTag.subarray(0, ctWithTag.length - 16);
  const d = crypto.createDecipheriv('aes-256-gcm', key, nonce);
  if (aad && aad.length) d.setAAD(aad);
  d.setAuthTag(tag);
  return Buffer.concat([d.update(ct), d.final()]);
}

function aesGcmEncrypt(key, plaintext, aad) {
  const nonce = crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-256-gcm', key, nonce);
  if (aad && aad.length) c.setAAD(aad);
  const ct = Buffer.concat([c.update(plaintext), c.final(), c.getAuthTag()]);
  return { nonce, ct };
}

function x25519PublicFromRaw(raw) {
  return crypto.createPublicKey({ key: { kty: 'OKP', crv: 'X25519', x: b64url(raw) }, format: 'jwk' });
}

function ed25519PublicFromRaw(raw) {
  return crypto.createPublicKey({ key: { kty: 'OKP', crv: 'Ed25519', x: b64url(raw) }, format: 'jwk' });
}

// Sigilla per il server: solo chi ha la chiave privata del server può aprire.
function sealFor(serverPubRaw, plaintext, aad) {
  const eph = crypto.generateKeyPairSync('x25519');
  const epk = Buffer.from(eph.publicKey.export({ format: 'jwk' }).x, 'base64url');
  const shared = crypto.diffieHellman({ privateKey: eph.privateKey, publicKey: x25519PublicFromRaw(serverPubRaw) });
  const key = Buffer.from(crypto.hkdfSync('sha256', shared, epk, SEAL_INFO, 32));
  const { nonce, ct } = aesGcmEncrypt(key, plaintext, aad);
  shared.fill(0); key.fill(0);
  return { epk: b64(epk), nonce: b64(nonce), ct: b64(ct) };
}

function verifyModule(signPubRaw, name, version, wasmBytes, signatureB64) {
  const sha = sha256hex(wasmBytes);
  const payload = Buffer.from(`alveare-module|${name}|${version}|${sha}`);
  return { ok: crypto.verify(null, payload, ed25519PublicFromRaw(signPubRaw), unb64(signatureB64)), sha };
}

module.exports = { b64, unb64, sha256hex, aesGcmDecrypt, aesGcmEncrypt, sealFor, verifyModule };
