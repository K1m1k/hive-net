// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// The bee's local panel: http://127.0.0.1:7777 (reachable from this PC only).
const fs = require('fs');
const http = require('http');
const path = require('path');

function startUi(bee, port) {
  const page = fs.readFileSync(path.join(__dirname, '..', 'ui', 'index.html'));
  const server = http.createServer(async (req, res) => {
    // local requests with the correct Host header only (protection against malicious websites in the browser)
    const host = (req.headers.host || '').split(':')[0];
    if (!['127.0.0.1', 'localhost'].includes(host)) { res.writeHead(403); return res.end(); }
    const send = (code, obj) => {
      res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' });
      res.end(JSON.stringify(obj));
    };
    if (req.method === 'GET' && (req.url === '/' || req.url === '/index.html')) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
        'content-security-policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'" });
      return res.end(page);
    }
    if (req.method === 'GET' && req.url === '/api/state') return send(200, bee.state());
    if (req.method === 'GET' && req.url === '/api/report') {
      const name = `bee-report-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.md`;
      res.writeHead(200, { 'content-type': 'text/markdown; charset=utf-8', 'cache-control': 'no-store',
        'content-disposition': `attachment; filename="${name}"` });
      return res.end(bee.perf.report(bee.cfg.node_name));
    }
    if (req.method === 'POST' && req.url === '/api/settings') {
      if (req.headers['x-hivenet'] !== '1') return send(403, { error: 'request not allowed' });
      let raw = '';
      for await (const ch of req) { raw += ch; if (raw.length > 100000) return send(413, {}); }
      try { bee.applySettings(JSON.parse(raw || '{}')); return send(200, bee.state()); } catch (e) { return send(400, { error: e.message }); }
    }
    send(404, { error: 'not found' });
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

module.exports = { startUi };
