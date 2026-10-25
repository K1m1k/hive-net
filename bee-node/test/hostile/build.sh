#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
# Builds the hostile modules used by test/sandbox.test.js (requires clang with the wasm32 target).
set -e
cd "$(dirname "$0")"
F="--target=wasm32 -O2 -nostdlib -fno-builtin -ffreestanding -Wl,--no-entry -Wl,--strip-all"
clang $F -DLOOP -Wl,--max-memory=16777216 -o loop.wasm hostile.c
clang $F -Wl,--max-memory=16777216 -o memory-capped.wasm hostile.c
clang $F -o memory-uncapped.wasm hostile.c
clang $F -Wl,--max-memory=4294967296 -o memory-huge.wasm hostile.c
clang $F -DIMPORTS -Wl,--allow-undefined -Wl,--max-memory=16777216 -o imports.wasm hostile.c
echo "hostile modules built"
