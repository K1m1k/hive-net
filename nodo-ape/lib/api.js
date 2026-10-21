'use strict';
// Chiamate al server. Messaggi brevi, solo quando servono (niente segnali periodici).
class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

class Api {
  constructor(server, token) {
    this.server = server.replace(/\/+$/, '');
    this.token = token;
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
