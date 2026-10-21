#!/bin/sh
# Prepara il server in un ambiente Python ISOLATO dentro la cartella del progetto
# (non tocca i pacchetti di sistema ne' gli altri progetti).
# Uso, dalla cartella principale del progetto:  sh scripts/installa-server.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip >/dev/null
"$ROOT/.venv/bin/pip" install -r "$ROOT/orchestratore/requirements.txt"
echo "Server pronto. Avvialo con: sh scripts/avvia-server.sh"
