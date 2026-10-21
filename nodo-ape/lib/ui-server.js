'use strict';
// Pannello locale dell'ape: http://127.0.0.1:7777 (solo da questo PC).
const fs = require('fs');
const http = require('http');
const path = require('path');

function startUi(bee, port) {
  const page = fs.readFileSync(path.join(__dirname, '..', 'ui', 'index.html'));
  const server = http.createServer(async (req, res) => {
    // solo richieste locali con Host corretto (protezione da siti malevoli nel browser)
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
    if (req.method === 'POST' && req.url === '/api/settings') {
      if (req.headers['x-alveare'] !== '1') return send(403, { error: 'richiesta non ammessa' });
      let raw = '';
      for await (const ch of req) { raw += ch; if (raw.length > 100000) return send(413, {}); }
      try { bee.applySettings(JSON.parse(raw || '{}')); return send(200, bee.state()); } catch (e) { return send(400, { error: e.message }); }
    }
    send(404, { error: 'non trovato' });
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

module.exports = { startUi };
