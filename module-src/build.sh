#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
# Compiles the C modules to WebAssembly (requires clang with the wasm32 target and wasm-ld).
# Usage: sh module-src/build.sh   (from the project root)
# --max-memory: every module declares a memory limit (256 MB); bees reject modules without one.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../orchestrator/modules"
mkdir -p "$OUT"
for m in text_clean pii_clean text_stats embed_hash; do
  clang --target=wasm32 -O2 -nostdlib -fno-builtin -ffreestanding \
    -Wl,--no-entry -Wl,--strip-all -Wl,--export=__heap_base -Wl,--max-memory=268435456 \
    -o "$OUT/$m.wasm" "$HERE/$m.c"
  echo "compiled $m.wasm"
done
