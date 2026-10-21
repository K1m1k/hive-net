'use strict';
// Esegue un pacchetto in un thread isolato, con memoria limitata e tempo massimo.
const path = require('path');
const { Worker } = require('worker_threads');

function runPacket({ wasm, key, nonce, ct, aad, serverPub, timeoutMs = 120000, memoryMb = 512 }) {
  return new Promise((resolve, reject) => {
    const w = new Worker(path.join(__dirname, 'sandbox-worker.js'), {
      workerData: { wasm, key, nonce, ct, aad, serverPub },
      resourceLimits: { maxOldGenerationSizeMb: memoryMb, maxYoungGenerationSizeMb: 64 },
      env: {}, // il thread non vede le variabili d'ambiente
      stdout: true, stderr: true,
    });
    let done = false;
    const timer = setTimeout(() => {
      if (!done) { done = true; w.terminate(); reject(new Error('tempo massimo superato')); }
    }, timeoutMs);
    w.once('message', (m) => {
      if (done) return;
      done = true; clearTimeout(timer); w.terminate();
      m.ok ? resolve(m) : reject(new Error(m.error));
    });
    w.once('error', (err) => { if (!done) { done = true; clearTimeout(timer); reject(err); } });
    w.once('exit', (code) => { if (!done) { done = true; clearTimeout(timer); reject(new Error('sandbox terminata: ' + code)); } });
  });
}

module.exports = { runPacket };
