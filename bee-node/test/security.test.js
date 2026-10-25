// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// Node security: HTTPS-only servers and a Node.js release with the known security fixes.
// Run: npm test
const test = require('node:test');
const assert = require('node:assert');
const { Api } = require('../lib/api');

test('the node only accepts HTTPS servers', () => {
  assert.doesNotThrow(() => new Api('https://hive.example.com'));
  assert.doesNotThrow(() => new Api('https://hive.example.com:8443/'));
});

test('HTTP is allowed only towards this same PC', () => {
  for (const u of ['http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000']) {
    assert.doesNotThrow(() => new Api(u), u);
  }
});

test('HTTP towards other machines is rejected (the token would travel in plain text)', () => {
  const prev = process.env.HIVENET_ALLOW_INSECURE;
  delete process.env.HIVENET_ALLOW_INSECURE;
  try {
    for (const u of ['http://192.168.1.10:8000', 'http://hive.example.com', 'http://127.0.0.1.example.com']) {
      assert.throws(() => new Api(u), /Insecure server/, u);
    }
  } finally {
    if (prev !== undefined) process.env.HIVENET_ALLOW_INSECURE = prev;
  }
});

test('invalid addresses and other protocols are rejected', () => {
  for (const u of ['', 'hive.example.com', 'ftp://hive.example.com', 'file:///etc/passwd', 'javascript:alert(1)']) {
    assert.throws(() => new Api(u), undefined, u);
  }
});

test('HTTP on a home network only with an explicit HIVENET_ALLOW_INSECURE=1', () => {
  const prev = process.env.HIVENET_ALLOW_INSECURE;
  const warn = console.warn;
  let warned = false;
  console.warn = () => { warned = true; };
  try {
    process.env.HIVENET_ALLOW_INSECURE = 'yes';
    assert.throws(() => new Api('http://192.168.1.10:8000'), /Insecure server/, 'only the value 1 counts');
    process.env.HIVENET_ALLOW_INSECURE = '1';
    assert.doesNotThrow(() => new Api('http://192.168.1.10:8000'));
    assert.ok(warned, 'it must warn that the connection is not encrypted');
  } finally {
    console.warn = warn;
    if (prev === undefined) delete process.env.HIVENET_ALLOW_INSECURE; else process.env.HIVENET_ALLOW_INSECURE = prev;
  }
});

// Releases with the July 2026 security fixes (HTTP/2, permission model, HTTP parser)
const MINIMUMS = { 22: [22, 23, 2], 24: [24, 18, 1], 26: [26, 5, 1] };
test('Node.js includes the known security fixes', () => {
  const v = process.versions.node.split('.').map(Number);
  assert.ok(v[0] >= 22, `Node ${process.version} is too old: at least 22.23.2 is required`);
  const min = MINIMUMS[v[0]];
  if (min) {
    const ok = v[1] > min[1] || (v[1] === min[1] && v[2] >= min[2]);
    assert.ok(ok, `Node ${process.version}: upgrade to at least ${min.join('.')}`);
  }
});
