#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
# Starts the HiveNet server on port 8000 (data in ./data).
# Usage, from the project root:  sh scripts/start-server.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HIVENET_DATA_DIR="${HIVENET_DATA_DIR:-$ROOT/data}"
cd "$ROOT/orchestrator"
exec "$ROOT/.venv/bin/python" -m hive_server --host 0.0.0.0 --port "${PORT:-8000}"
