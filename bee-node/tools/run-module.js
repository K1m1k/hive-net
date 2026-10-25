#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
'use strict';
// Runs a WebAssembly module on a file (or stdin) and writes the result to stdout.
// Handy for checks and tests: node tools/run-module.js path/to/module.wasm [input-file]
const fs = require('fs');

const [wasmPath, inputPath] = process.argv.slice(2);
if (!wasmPath) { console.error('usage: run-module.js module.wasm [input]'); process.exit(1); }
const input = inputPath ? fs.readFileSync(inputPath) : fs.readFileSync(0);
const e = new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(wasmPath)), {}).exports;
e.reset();
const p = e.alloc(input.length);
new Uint8Array(e.memory.buffer).set(input, p);
const o = e.run(p, input.length);
process.stdout.write(Buffer.from(new Uint8Array(e.memory.buffer, o, e.out_len())));
