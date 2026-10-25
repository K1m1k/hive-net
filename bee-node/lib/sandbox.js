// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// Runs packets in a separate PROCESS, with limited memory and a time limit.
//
// Why a process rather than a thread: a thread stuck inside WebAssembly sometimes cannot be
// stopped and keeps burning CPU; a process can always be stopped by the operating system
// (SIGKILL). So once the time is up, nothing is left running on the PC.
//
// Before running, the memory limit declared by the module is checked: a module with no limit,
// or with a limit above the allowed one, is rejected.
const path = require('path');
const { fork } = require('child_process');

const PAGE = 65536;
const DEFAULT_WASM_MAX_MB = 256;

function readLeb(buf, pos) {
  let result = 0, shift = 0, b;
  do {
    if (pos >= buf.length) throw new Error('malformed module');
    b = buf[pos++];
    result += (b & 0x7f) * 2 ** shift;
    shift += 7;
  } while (b & 0x80 && shift < 35);
  return [result, pos];
}

// Memory limit declared by the module, in bytes (null = no limit declared)
function wasmMemoryMax(bytes) {
  const buf = Buffer.from(bytes);
  if (buf.length < 8 || buf.readUInt32LE(0) !== 0x6d736100) throw new Error('not a WebAssembly module');
  let pos = 8;
  let found;
  while (pos < buf.length) {
    const id = buf[pos++];
    let size;
    [size, pos] = readLeb(buf, pos);
    const end = pos + size;
    if (id === 2) throw new Error('the module imports functions from the system: rejected');
    if (id === 5) {
      let count, p = pos;
      [count, p] = readLeb(buf, p);
      if (count !== 1) throw new Error('the module must have exactly one memory');
      const flags = buf[p++];
      if (flags & ~0x01) throw new Error('shared or 64-bit memory is not allowed');
      let min;
      [min, p] = readLeb(buf, p);
      if (!(flags & 0x01)) { found = null; } else { const [max] = readLeb(buf, p); found = max * PAGE; }
      if (min * PAGE > (found ?? Infinity)) throw new Error('malformed module');
    }
    pos = end;
  }
  if (found === undefined) throw new Error('the module has no memory');
  return found;
}

// A single sandbox process, reused from one cell to the next (starting it costs more than the work).
// It is killed and recreated when a cell times out or fails; it shuts itself down when idle so
// it does not hold on to memory; it is recycled every so often to start afresh.
const IDLE_MS = 60000;
const RECYCLE_AFTER = 500;

class Sandbox {
  constructor() { this.child = null; this.busy = Promise.resolve(); this.seq = 0; this.used = 0; this.idleTimer = null; }

  spawn(memoryMb) {
    const child = fork(path.join(__dirname, 'sandbox-worker.js'), [], {
      env: {},                       // the process cannot see the environment variables
      execArgv: [`--max-old-space-size=${memoryMb}`, '--max-semi-space-size=16'],
      serialization: 'advanced',     // Buffers are passed without conversion
      stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
    });
    child.memoryMb = memoryMb;
    child.once('exit', () => { if (this.child === child) this.child = null; });
    child.on('error', () => {});
    child.unref();                   // does not keep the bee program alive
    child.channel && child.channel.unref();
    this.used = 0;
    return child;
  }

  kill() {
    const c = this.child;
    this.child = null;
    if (c && c.exitCode === null && c.signalCode === null) c.kill('SIGKILL');
  }

  run(data, timeoutMs, memoryMb) {
    // one cell at a time: the next one waits for the previous one to finish
    const job = this.busy.then(() => this._run(data, timeoutMs, memoryMb));
    this.busy = job.catch(() => {});
    return job;
  }

  _run(data, timeoutMs, memoryMb) {
    clearTimeout(this.idleTimer);
    if (this.child && (this.child.memoryMb !== memoryMb || this.used >= RECYCLE_AFTER)) this.kill();
    let started = 0;
    if (!this.child) { const t = Date.now(); this.child = this.spawn(memoryMb); started = Date.now() - t; }
    const child = this.child;
    child.channel && child.channel.ref();
    this.used++;
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = (ok, v) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        child.off('message', onMsg);
        child.off('exit', onExit);
        if (!ok) this.kill();        // after an error or a timeout, start again from a fresh process
        if (this.child) {
          this.child.channel && this.child.channel.unref();
          this.idleTimer = setTimeout(() => this.kill(), IDLE_MS);
          this.idleTimer.unref();
        }
        ok ? resolve(v) : reject(v);
      };
      const onMsg = (m) => {
        if (!m || m.id !== id) return;
        if (m.ok) { m.startMs = started; finish(true, m); } else finish(false, new Error(m.error || 'sandbox error'));
      };
      const onExit = (code, sig) => finish(false, new Error(`sandbox exited: ${sig || code}`));
      const timer = setTimeout(() => finish(false, new Error('time limit exceeded')), timeoutMs);
      child.on('message', onMsg);
      child.once('exit', onExit);
      try { child.send({ id, ...data }); } catch (e) { finish(false, e); }
    });
  }
}

const sandbox = new Sandbox();

function runPacket({ wasm, key, nonce, ct, aad, serverPub, timeoutMs = 120000, memoryMb = 512,
  wasmMaxMb = DEFAULT_WASM_MAX_MB }) {
  let max;
  try { max = wasmMemoryMax(wasm); } catch (e) { return Promise.reject(e); }
  if (max === null) return Promise.reject(new Error('the module does not declare a memory limit: rejected'));
  if (max > wasmMaxMb * 1048576) {
    return Promise.reject(new Error(`the module asks for up to ${Math.round(max / 1048576)} MB (limit ${wasmMaxMb} MB): rejected`));
  }
  return sandbox.run({ wasm: Buffer.from(wasm), key: Buffer.from(key), nonce: Buffer.from(nonce), ct: Buffer.from(ct),
    aad: Buffer.from(aad), serverPub: Buffer.from(serverPub) }, timeoutMs, memoryMb);
}

function stopSandbox() { sandbox.kill(); }

module.exports = { runPacket, wasmMemoryMax, stopSandbox };
