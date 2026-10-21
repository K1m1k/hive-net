#!/bin/sh
# Compila i moduli C in WebAssembly (serve clang con target wasm32 e wasm-ld).
# Uso: sh moduli-sorgenti/build.sh   (dalla cartella principale del progetto)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../orchestratore/modules"
mkdir -p "$OUT"
for m in text_clean pii_clean text_stats embed_hash; do
  clang --target=wasm32 -O2 -nostdlib -fno-builtin -ffreestanding \
    -Wl,--no-entry -Wl,--strip-all -Wl,--export=__heap_base \
    -o "$OUT/$m.wasm" "$HERE/$m.c"
  echo "compilato $m.wasm"
done
