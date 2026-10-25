// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// The bee's performance metrics: how long each phase of a cell takes, how many
// bytes travel, how much CPU and memory the node uses.
// ONLY the cell's timings and byte counts reach the server (together with the result,
// no extra messages); load from other programs, PC usage and battery stay on this computer.
const fs = require('fs');
const os = require('os');

const KEEP = 500;
// phases of a cell, in the order they happen
const PHASES = [
  ['download_ms', 'Packet download'],
  ['wait_ms', 'Queued on the node'],
  ['key_ms', 'Key request'],
  ['sandbox_ms', 'Sandbox start-up'],
  ['decrypt_ms', 'Decryption'],
  ['compile_ms', 'Module preparation'],
  ['wasm_ms', 'Computation (WebAssembly module)'],
  ['seal_ms', 'Result sealing'],
  ['upload_ms', 'Result delivery'],
];

const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
function pct(a, p) {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
}
const r1 = (x) => Math.round(x * 10) / 10;

class Perf {
  constructor(file) {
    this.file = file;
    this.cells = [];
    try { this.cells = JSON.parse(fs.readFileSync(file, 'utf8')).slice(-KEEP); } catch { /* first start */ }
    this.startedAt = Date.now();
  }

  add(cell) {
    this.cells.push(cell);
    if (this.cells.length > KEEP) this.cells.splice(0, this.cells.length - KEEP);
    try { fs.writeFileSync(this.file, JSON.stringify(this.cells), { mode: 0o600 }); } catch { /* never blocking */ }
  }

  summary(last = 100) {
    const c = this.cells.slice(-last);
    const n = c.length;
    const all = PHASES.map(([k, label]) => {
      const v = c.map((x) => x[k] || 0);
      return { key: k, label, avg_ms: r1(avg(v)), p95_ms: r1(pct(v, 95)), total_ms: Math.round(v.reduce((s, x) => s + x, 0)) };
    });
    // queueing is not a cost: meanwhile the node is working on another packet
    const wait = all.find((p) => p.key === 'wait_ms');
    const phases = all.filter((p) => p.key !== 'wait_ms');
    const tot = phases.reduce((s, p) => s + p.total_ms, 0) || 1;
    for (const p of phases) p.share = r1((100 * p.total_ms) / tot);
    const work = phases.filter((p) => p.key === 'wasm_ms').reduce((s, p) => s + p.total_ms, 0);
    const down = c.reduce((s, x) => s + (x.down_bytes || 0), 0);
    const downMs = c.reduce((s, x) => s + (x.download_ms || 0), 0);
    const up = c.reduce((s, x) => s + (x.up_bytes || 0), 0);
    const upMs = c.reduce((s, x) => s + (x.upload_ms || 0), 0);
    const cpu = c.reduce((s, x) => s + (x.cpu_ms || 0), 0);
    const wall = c.reduce((s, x) => s + (x.cell_ms || 0), 0);
    const worst = [...phases].filter((p) => p.key !== 'wasm_ms').sort((a, b) => b.total_ms - a.total_ms)[0];
    const bottleneck = work >= worst.total_ms ? 'Computation (healthy)' : worst.label;
    return {
      cells: n,
      phases,
      useful_share: r1((100 * work) / tot),
      bottleneck: n ? bottleneck : null,
      wait_avg_ms: wait.avg_ms,
      down_kbs: downMs ? r1(down / 1024 / (downMs / 1000)) : 0,
      up_kbs: upMs ? r1(up / 1024 / (upMs / 1000)) : 0,
      down_kb: r1(down / 1024), up_kb: r1(up / 1024),
      // CPU used while processing cells, in "busy cores" (1 = one core at 100%)
      cpu_cores: wall ? Math.round((cpu / wall) * 100) / 100 : 0,
      rss_mb: n ? r1(avg(c.map((x) => x.rss_mb || 0))) : r1(process.memoryUsage().rss / 1048576),
      rss_max_mb: n ? r1(Math.max(...c.map((x) => x.rss_mb || 0))) : 0,
      wasm_mem_mb: n ? r1(Math.max(...c.map((x) => x.wasm_mem_mb || 0))) : 0,
      // texts per second of active time (computation plus distribution overhead)
      records_s: tot > 1 ? r1(c.reduce((s, x) => s + (x.records || 0), 0) / (tot / 1000)) : 0,
      cores: os.cpus().length,
      recent: c.slice(-10).reverse().map((x) => ({ at: x.at, op: x.op, cell_ms: x.cell_ms, wasm_ms: x.wasm_ms, down_bytes: x.down_bytes, up_bytes: x.up_bytes })),
    };
  }

  report(nodeName) {
    const s = this.summary(KEEP);
    const L = [];
    L.push(`# Performance report for bee "${nodeName}"`, '');
    L.push(`Generated: ${new Date().toISOString()}  `);
    L.push(`System: ${process.platform} ${os.arch()}, ${os.cpus().length} cores (${os.cpus()[0] ? os.cpus()[0].model : '?'}), ${r1(os.totalmem() / 1073741824)} GB RAM, Node ${process.version}  `);
    L.push(`Cells measured: ${s.cells}`, '');
    if (!s.cells) { L.push('No cells completed yet.'); return L.join('\n'); }
    L.push('## Where the time goes', '', '| Phase | Mean (ms) | 95th percentile (ms) | Total (ms) | Share |', '|---|---:|---:|---:|---:|');
    for (const p of s.phases) L.push(`| ${p.label} | ${p.avg_ms} | ${p.p95_ms} | ${p.total_ms} | ${p.share}% |`);
    L.push('', `**Useful work (computation) as a share of active time: ${s.useful_share}%** · bottleneck: **${s.bottleneck}**  `);
    L.push(`Mean time queued on the node (not counted, as the node keeps working meanwhile): ${s.wait_avg_ms} ms`, '');
    L.push('## Network and resources', '');
    L.push(`- Received ${s.down_kb} KB at ${s.down_kbs} KB/s · sent ${s.up_kb} KB at ${s.up_kbs} KB/s`);
    L.push(`- CPU while processing cells: ${s.cpu_cores} busy cores out of ${s.cores}`);
    L.push(`- Program memory: mean ${s.rss_mb} MB, peak ${s.rss_max_mb} MB · module memory: up to ${s.wasm_mem_mb} MB`);
    L.push(`- Real speed: ${s.records_s} texts per second of active time`, '');
    L.push('## Latest cells', '', '| Time | Operation | Texts | Duration (ms) | Computation (ms) | Bytes received | Bytes sent |', '|---|---|---:|---:|---:|---:|---:|');
    for (const x of this.cells.slice(-30).reverse()) {
      L.push(`| ${new Date(x.at).toISOString().slice(11, 19)} | ${x.op} | ${x.records || 0} | ${x.cell_ms} | ${x.wasm_ms} | ${x.down_bytes} | ${x.up_bytes} |`);
    }
    return L.join('\n') + '\n';
  }
}

module.exports = { Perf, PHASES };
