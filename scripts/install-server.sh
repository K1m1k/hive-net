#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
# Sets up the server in an ISOLATED Python environment inside the project directory
# (it never touches system packages or other projects).
# Usage, from the project root:  sh scripts/install-server.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip >/dev/null
"$ROOT/.venv/bin/pip" install -r "$ROOT/orchestrator/requirements.txt"
echo "Server ready. Start it with: sh scripts/start-server.sh"
