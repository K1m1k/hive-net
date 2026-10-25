# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Starts EVERYTHING for a first round of testing, on a single computer:
  - the orchestrator (server) on http://localhost:8000
  - two test bees with their panels on http://127.0.0.1:7771 and :7772
  - the test users (a requester plus the two bee owners)
  - optional (--sample): a sample job already submitted

Usage, from the project root:
  .venv/bin/python scripts/full_demo.py [--sample] [--bees N]
Ctrl+C stops everything. Demo data lives in ./demo-data (safe to delete).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "demo-data"
PASSWORD = "demo-password"
URL = "http://127.0.0.1:8000"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(URL + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json",
                                          **({"authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_status": e.code, **json.loads(e.read() or b"{}")}


def user(name):
    r = call("POST", "/api/auth/register", {"username": name, "password": PASSWORD})
    if "token" not in r:
        r = call("POST", "/api/auth/login", {"username": name, "password": PASSWORD})
    return r["token"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="submit a sample job straight away")
    ap.add_argument("--bees", type=int, default=2, help="how many test bees to start (1-5)")
    a = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    procs = []

    def stop(*_):
        print("\nShutting down: the bees are handing back the work they have not started...")
        for p in reversed(procs):
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(8)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    env = dict(os.environ, HIVENET_DATA_DIR=str(DATA / "orchestrator"), PYTHONPATH=str(ROOT / "orchestrator"))
    procs.append(subprocess.Popen([sys.executable, "-m", "hive_server", "--host", "0.0.0.0", "--port", "8000"],
                                  cwd=ROOT / "orchestrator", env=env,
                                  stdout=open(DATA / "orchestrator.log", "a"), stderr=subprocess.STDOUT))
    for _ in range(100):
        try:
            call("GET", "/api/public/stats")
            break
        except Exception:
            time.sleep(0.2)
    else:
        print("The orchestrator did not start: see", DATA / "orchestrator.log")
        stop()

    requester = user("demo")
    n = max(1, min(5, a.bees))
    for i in range(1, n + 1):
        home = DATA / f"bee{i}"
        cfg_file = home / "node.json"
        if not cfg_file.exists():
            tok = user(f"owner{i}")
            reg = call("POST", "/api/nodes", {"name": f"Demo bee {i}", "os": sys.platform}, tok)
            home.mkdir(exist_ok=True)
            cfg_file.write_text(json.dumps({
                "server": URL, "node_id": reg["node_id"], "node_token": reg["node_token"], "node_name": f"Demo bee {i}",
                "seal_public_key": reg["seal_public_key"], "sign_public_key": reg["sign_public_key"],
                # for the demo: every hour, and it does not stop while you use the PC (this cannot be detected in a Codespace)
                "schedule": [[True] * 24] * 7, "pause_when_user_active": False, "intensity": 3, "ui_port": 7770 + i,
            }, indent=2))
        procs.append(subprocess.Popen(["node", "hivenet-bee.js", "start"], cwd=ROOT / "bee-node",
                                      env=dict(os.environ, HIVENET_NODE_HOME=str(home)),
                                      stdout=open(DATA / f"bee{i}.log", "a"), stderr=subprocess.STDOUT))

    if a.sample:
        raw = (ROOT / "examples" / "sample-reviews.txt").read_bytes()
        # just like the browser does: personal data is removed BEFORE sending
        clean = subprocess.run(["node", str(ROOT / "bee-node" / "tools" / "run-module.js"),
                                str(ROOT / "orchestrator" / "modules" / "pii_clean.wasm")],
                               input=raw, capture_output=True, check=True).stdout.decode()
        job = call("POST", "/api/jobs", {"name": "Sample reviews", "ops": ["clean", "stats", "embed"], "text": clean},
                   requester)
        print("Sample job submitted:", job.get("id"), job.get("error", ""))

    print(f"""
HiveNet demo started.

  Web panel:     {URL}        (user: demo   password: {PASSWORD})
  Bee owners:    owner1, owner2 ...   password: {PASSWORD}
""" + "\n".join(f"  Bee {i} panel:   http://127.0.0.1:{7770 + i}" for i in range(1, n + 1)) + f"""

  Logs: {DATA}
  Press Ctrl+C to stop everything.
""")
    while True:
        time.sleep(1)
        for p in procs:
            if p.poll() is not None and p is procs[0]:
                print("The orchestrator stopped: see", DATA / "orchestrator.log")
                stop()


if __name__ == "__main__":
    main()
