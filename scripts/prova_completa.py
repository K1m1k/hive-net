"""Avvia TUTTO per la prima fase di test, su un solo computer:
  - l'orchestratore (server) su http://localhost:8000
  - due api di prova con i loro pannelli su http://127.0.0.1:7771 e :7772
  - gli utenti di prova (richiedente + due proprietari delle api)
  - facoltativo (--esempio): un lavoro di esempio gia' inviato

Uso, dalla cartella principale del progetto:
  .venv/bin/python scripts/prova_completa.py [--esempio] [--api N]
Si ferma tutto con Ctrl+C.  I dati di prova stanno in ./data-prova (si possono cancellare).
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
DATA = ROOT / "data-prova"
PASSWORD = "provaprova"
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
    ap.add_argument("--esempio", action="store_true", help="invia subito un lavoro di esempio")
    ap.add_argument("--api", type=int, default=2, help="quante api di prova avviare (1-5)")
    a = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    procs = []

    def stop(*_):
        print("\nArresto: le api restituiscono il lavoro non iniziato...")
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

    env = dict(os.environ, ALVEARE_DATA_DIR=str(DATA / "orchestratore"), PYTHONPATH=str(ROOT / "orchestratore"))
    procs.append(subprocess.Popen([sys.executable, "-m", "alveare_server", "--host", "0.0.0.0", "--port", "8000"],
                                  cwd=ROOT / "orchestratore", env=env,
                                  stdout=open(DATA / "orchestratore.log", "a"), stderr=subprocess.STDOUT))
    for _ in range(100):
        try:
            call("GET", "/api/public/stats")
            break
        except Exception:
            time.sleep(0.2)
    else:
        print("L'orchestratore non parte: guarda", DATA / "orchestratore.log")
        stop()

    richiedente = user("prova")
    n = max(1, min(5, a.api))
    for i in range(1, n + 1):
        home = DATA / f"ape{i}"
        cfg_file = home / "node.json"
        if not cfg_file.exists():
            tok = user(f"proprietario{i}")
            reg = call("POST", "/api/nodes", {"name": f"Ape di prova {i}", "os": sys.platform}, tok)
            home.mkdir(exist_ok=True)
            cfg_file.write_text(json.dumps({
                "server": URL, "node_id": reg["node_id"], "node_token": reg["node_token"], "node_name": f"Ape di prova {i}",
                "seal_public_key": reg["seal_public_key"], "sign_public_key": reg["sign_public_key"],
                # per la prova: tutte le ore, e non si ferma se usi il PC (in un Codespace non si puo' rilevare)
                "schedule": [[True] * 24] * 7, "pause_when_user_active": False, "intensity": 3, "ui_port": 7770 + i,
            }, indent=2))
        procs.append(subprocess.Popen(["node", "alveare-node.js", "start"], cwd=ROOT / "nodo-ape",
                                      env=dict(os.environ, ALVEARE_NODE_HOME=str(home)),
                                      stdout=open(DATA / f"ape{i}.log", "a"), stderr=subprocess.STDOUT))

    if a.esempio:
        raw = (ROOT / "esempi" / "recensioni-esempio.txt").read_bytes()
        # come fa il browser: i dati personali si tolgono PRIMA di inviare
        clean = subprocess.run(["node", str(ROOT / "nodo-ape" / "tools" / "run-module.js"),
                                str(ROOT / "orchestratore" / "modules" / "pii_clean.wasm")],
                               input=raw, capture_output=True, check=True).stdout.decode()
        job = call("POST", "/api/jobs", {"name": "Recensioni di esempio", "ops": ["clean", "stats", "embed"], "text": clean},
                   richiedente)
        print("Lavoro di esempio inviato:", job.get("id"), job.get("error", ""))

    print(f"""
Alveare di prova avviato.

  Pannello web:     {URL}        (utente: prova   password: {PASSWORD})
  Proprietari api:  proprietario1, proprietario2 ...   password: {PASSWORD}
""" + "\n".join(f"  Pannello ape {i}:   http://127.0.0.1:{7770 + i}" for i in range(1, n + 1)) + f"""

  Registri: {DATA}
  Ferma tutto con Ctrl+C.
""")
    while True:
        time.sleep(1)
        for p in procs:
            if p.poll() is not None and p is procs[0]:
                print("L'orchestratore si e' fermato: guarda", DATA / "orchestratore.log")
                stop()


if __name__ == "__main__":
    main()
