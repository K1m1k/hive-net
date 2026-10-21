#!/usr/bin/env node
'use strict';
// Esegue un modulo WebAssembly su un file (o stdin) e scrive il risultato su stdout.
// Utile per controlli e test: node tools/run-module.js percorso/modulo.wasm [file-ingresso]
const fs = require('fs');

const [wasmPath, inputPath] = process.argv.slice(2);
if (!wasmPath) { console.error('uso: run-module.js modulo.wasm [ingresso]'); process.exit(1); }
const input = inputPath ? fs.readFileSync(inputPath) : fs.readFileSync(0);
const e = new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(wasmPath)), {}).exports;
e.reset();
const p = e.alloc(input.length);
new Uint8Array(e.memory.buffer).set(input, p);
const o = e.run(p, input.length);
process.stdout.write(Buffer.from(new Uint8Array(e.memory.buffer, o, e.out_len())));
