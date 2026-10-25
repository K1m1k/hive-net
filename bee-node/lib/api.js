// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// Calls to the server. Short messages, only when needed (no periodic heartbeats).
class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

class Api {
  constructor(server, token) {
    this.server = Api.checkServer(server).replace(/\/+$/, '');
    this.token = token;
  }

  // HTTPS only, except for this same PC. For tests on a home network: HIVENET_ALLOW_INSECURE=1
  static checkServer(server) {
    let u;
    try { u = new URL(server); } catch { throw new Error(`Invalid server address: ${server}`); }
    if (u.protocol === 'https:') return server;
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname);
    if (u.protocol === 'http:' && local) return server;
    if (u.protocol === 'http:' && process.env.HIVENET_ALLOW_INSECURE === '1') {
      console.warn('WARNING: the connection to the server is NOT encrypted (HIVENET_ALLOW_INSECURE=1). For testing only.');
      return server;
    }
    throw new Error(`Insecure server: ${server}. Use an https:// address`);
  }

  async call(method, path, body, { timeoutMs = 30000, raw = false } = {}) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(this.server + path, {
        method,
        headers: {
          'content-type': 'application/json',
          ...(this.token ? { authorization: 'Bearer ' + this.token } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      });
      if (raw) {
        if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
        return Buffer.from(await res.arrayBuffer());
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(data.error || `HTTP ${res.status}`, res.status);
      return data;
    } finally {
      clearTimeout(t);
    }
  }

  post(path, body, opts) { return this.call('POST', path, body || {}, opts); }
  get(path, opts) { return this.call('GET', path, undefined, opts); }
}

module.exports = { Api, ApiError };
