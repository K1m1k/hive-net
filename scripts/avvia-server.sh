#!/bin/sh
# Avvia il server Alveare sulla porta 8000 (dati in ./data).
# Uso, dalla cartella principale del progetto:  sh scripts/avvia-server.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ALVEARE_DATA_DIR="${ALVEARE_DATA_DIR:-$ROOT/data}"
cd "$ROOT/orchestratore"
exec "$ROOT/.venv/bin/python" -m alveare_server --host 0.0.0.0 --port "${PORT:-8000}"
