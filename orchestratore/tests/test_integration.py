"""Collaudo completo: server vero + nodi veri (processi separati).

Avvio:  cd server && python3 -m unittest discover -s tests -v
Serve Node.js >= 20 per i nodi.

Scenari:
  1. 10.000 testi, un nodo spento a meta': nessuna perdita, risultato identico al calcolo su un solo PC
  2. consegne duplicate, reinvii e prestiti scaduti: niente doppi conteggi
  3. nodo che bara scoperto dalla verifica incrociata e penalizzato
  4. prova del nove: errore segnalato dal richiedente, confermato da due api, corretto
"""
import base64
import json
import os
import random
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "orchestratore"
NODE_DIR = ROOT / "nodo-ape"
MODULES = SERVER_DIR / "modules"
sys.path.insert(0, str(SERVER_DIR))
from alveare_server.crypto import seal_for, sha256_hex  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_module(name: str, data: bytes) -> bytes:
    return subprocess.run(["node", str(NODE_DIR / "tools" / "run-module.js"), str(MODULES / f"{name}.wasm")],
                          input=data, capture_output=True, check=True).stdout


def make_text(n: int, seed: int) -> str:
    rnd = random.Random(seed)
    words = "il la un una gatto cane casa mare sole luna strada treno Roma Milano corre dorme mangia legge scrive".split()
    lines = [" ".join(rnd.choice(words) for _ in range(rnd.randint(3, 14))) for _ in range(n)]
    for i in range(0, n, 97):
        lines[i] = "  " + lines[i].upper() + "   "
    for i in range(5, n, 50):
        lines[i] = lines[i - 5]  # doppioni
    return "\n".join(lines)


class Hive:
    """Avvia un server Alveare in una cartella temporanea."""

    def __init__(self, **env):
        self.tmp = Path(tempfile.mkdtemp(prefix="alveare-test-"))
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        e = dict(os.environ, ALVEARE_DATA_DIR=str(self.tmp / "data"), ALVEARE_MAINTENANCE_EVERY_S="0.5",
                 ALVEARE_LEASE_MIN_S="2", ALVEARE_LEASE_MAX_S="6", ALVEARE_CHUNK_MAX_BYTES="16384",
                 ALVEARE_WELCOME_MIELE="1000", PYTHONPATH=str(SERVER_DIR))
        e.update({f"ALVEARE_{k.upper()}": str(v) for k, v in env.items()})
        self.files = [open(self.tmp / "server.log", "w")]
        self._db = None
        self.proc = subprocess.Popen([sys.executable, "-m", "alveare_server", "--port", str(self.port)],
                                     cwd=SERVER_DIR, env=e, stdout=self.files[0], stderr=subprocess.STDOUT)
        self.nodes = []
        for _ in range(100):
            try:
                self.get("/api/public/stats")
                break
            except Exception:
                time.sleep(0.1)

    @property
    def db(self):
        if self._db is None:
            self._db = sqlite3.connect(self.tmp / "data" / "alveare.db", isolation_level=None)
            self._db.row_factory = sqlite3.Row
        return self._db

    def req(self, method, path, body=None, token=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.url + path, data=data, method=method,
                                   headers={"content-type": "application/json",
                                            **({"authorization": "Bearer " + token} if token else {})})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                b = resp.read()
                return b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            return {"_status": e.code, **json.loads(e.read() or b"{}")}

    def get(self, path, token=None, raw=False):
        return self.req("GET", path, None, token, raw)

    def post(self, path, body, token=None):
        return self.req("POST", path, body, token)

    def user(self, name):
        return self.post("/api/auth/register", {"username": name, "password": "password123"})["token"]

    def node(self, user_token, name):
        return self.post("/api/nodes", {"name": name, "os": "test"}, user_token)

    def start_node(self, reg):
        home = self.tmp / f"node-{reg['node_id']}"
        home.mkdir()
        cfg = {"server": self.url, "node_id": reg["node_id"], "node_token": reg["node_token"], "node_name": f"n{reg['node_id']}",
               "seal_public_key": reg["seal_public_key"], "sign_public_key": reg["sign_public_key"],
               "schedule": [[True] * 24] * 7, "pause_when_user_active": False, "intensity": 5, "ui_port": free_port()}
        (home / "node.json").write_text(json.dumps(cfg))
        log = open(home / "log.txt", "w")
        self.files.append(log)
        p = subprocess.Popen(["node", "alveare-node.js", "start"], cwd=NODE_DIR,
                             env=dict(os.environ, ALVEARE_NODE_HOME=str(home)),
                             stdout=log, stderr=subprocess.STDOUT)
        self.nodes.append(p)
        return p

    def wait_job(self, tok, jid, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.get(f"/api/jobs/{jid}", tok)
            if j["status"] in ("done", "failed"):
                return j
            time.sleep(0.5)
        raise AssertionError(f"lavoro {jid} non finito: {j}")

    def close(self):
        for p in self.nodes:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in self.nodes:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.proc.terminate()
        self.proc.wait(5)
        if self._db is not None:
            self._db.close()
        for f in self.files:
            f.close()

    def assert_ledger_ok(self, tc):
        c = self.db
        tc.assertEqual(c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_entries").fetchone()[0], 0,
                       "il libro del miele deve sommare a zero")
        bad = c.execute("""SELECT tx_id, SUM(amount) s FROM ledger_entries GROUP BY tx_id HAVING s != 0""").fetchall()
        tc.assertEqual(bad, [], "ogni movimento deve essere bilanciato")
        dup = c.execute("SELECT task_id, COUNT(*) n FROM work_log GROUP BY task_id HAVING n > 1").fetchall()
        tc.assertEqual(dup, [], "nessuna celletta pagata due volte")


def expected_results(text: str):
    recs = text.split("\n")
    clean = run_module("text_clean", text.encode()).decode().split("\n")
    assert len(clean) == len(recs)
    uniq = list(dict.fromkeys(t for t in clean if t))
    emb = np.frombuffer(run_module("embed_hash", "\n".join(uniq).encode()), "<f4").reshape(len(uniq), 256)
    return clean, {sha256_hex(t.encode()): emb[i].astype("<f2").tobytes() for i, t in enumerate(uniq)}


def check_results(tc, hive, tok, jid, text):
    clean, emb = expected_results(text)
    lines = hive.get(f"/api/jobs/{jid}/result?embeddings=0", tok, raw=True).decode().strip().split("\n")
    got = [json.loads(x) for x in lines]
    tc.assertEqual(len(got), len(clean))
    tc.assertEqual([g["clean"] for g in got], clean, "pulizia diversa dal calcolo su un solo PC")
    c = hive.db
    for h, vec in emb.items():
        row = c.execute("SELECT embedding FROM items WHERE content_hash=?", (h,)).fetchone()
        tc.assertIsNotNone(row)
        tc.assertEqual(row[0], vec, "mappa dei significati diversa dal calcolo su un solo PC")


class T1_FaultTolerance(unittest.TestCase):
    def test_10000_testi_nodo_spento(self):
        h = Hive(verify_p_new=0.3)
        try:
            req = h.user("richiedente")
            regs = [h.node(h.user(f"ape{i}"), f"ape{i}") for i in range(3)]
            text = make_text(10000, 1)
            job = h.post("/api/jobs", {"name": "diecimila", "ops": ["clean", "stats", "embed"], "text": text}, req)
            procs = [h.start_node(r) for r in regs]
            # spegne di colpo il primo nodo appena ha preso del lavoro
            for _ in range(200):
                if h.db.execute("SELECT COUNT(*) FROM tasks WHERE status='leased' AND node_id=?",
                                (regs[0]["node_id"],)).fetchone()[0]:
                    break
                time.sleep(0.05)
            procs[0].kill()
            j = h.wait_job(req, job["id"], 180)
            self.assertEqual(j["status"], "done")
            self.assertEqual(j["records"], 10000)
            check_results(self, h, req, job["id"], text)
            c = h.db
            self.assertEqual(c.execute("SELECT COUNT(*) FROM staging").fetchone()[0], 0, "originali non cancellati")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM chunks WHERE input_blob IS NOT NULL").fetchone()[0], 0)
            h.assert_ledger_ok(self)
            # stesso lavoro di nuovo: tutto dalla memoria, nessuna celletta distribuita
            before = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            job2 = h.post("/api/jobs", {"name": "di nuovo", "ops": ["clean", "stats", "embed"], "text": text}, req)
            j2 = h.wait_job(req, job2["id"], 30)
            self.assertEqual(j2["status"], "done")
            self.assertTrue(j2["reused"])
            self.assertEqual(h.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], before, "lavoro rifatto inutilmente")
            self.assertLess(j2["cost"], j["cost"])
        finally:
            h.close()


class T2_Protocol(unittest.TestCase):
    def test_duplicati_reinvii_scadenze(self):
        h = Hive(lease_max_s=3, lease_min_s=2)
        try:
            req = h.user("richiedente")
            reg = h.node(h.user("manuale"), "manuale")
            nt = reg["node_token"]
            seal_pub = base64.b64decode(reg["seal_public_key"])
            h.post("/api/jobs", {"name": "p", "ops": ["clean"], "text": make_text(3000, 2)}, req)
            pk = h.post("/api/node/work", {"max_packets": 2, "lease_seconds": 3}, nt)["packets"]
            self.assertEqual(len(pk), 2)
            p = pk[0]
            self.assertNotIn("key", p, "la chiave non deve viaggiare con il pacchetto")
            key = base64.b64decode(h.post("/api/node/key", {"task_id": p["task_id"], "lease_token": p["lease_token"]}, nt)["key"])
            aad = base64.b64decode(p["aad"])
            plain = AESGCM(key).decrypt(base64.b64decode(p["nonce"]), base64.b64decode(p["ct"]), aad)
            out = run_module("text_clean", plain)
            body = {"task_id": p["task_id"], "lease_token": p["lease_token"], "idem_key": "k-1",
                    "sealed": seal_for(seal_pub, out, aad), "result_sha256": sha256_hex(out)}
            self.assertEqual(h.post("/api/node/result", body, nt)["status"], "accepted")
            self.assertEqual(h.post("/api/node/result", body, nt)["status"], "duplicate")
            body2 = dict(body, idem_key="k-2")
            self.assertEqual(h.post("/api/node/result", body2, nt)["status"], "rejected", "reinvio con altra chiave")
            # pacchetto con AAD manomessa: il sigillo non si apre
            q = pk[1]
            bad = dict(body, task_id=q["task_id"], lease_token=q["lease_token"], idem_key="k-3")
            self.assertEqual(h.post("/api/node/result", bad, nt)["status"], "rejected")
            # prestito scaduto: chiave negata e consegna rifiutata
            more = h.post("/api/node/work", {"max_packets": 1, "lease_seconds": 2}, nt)["packets"]
            r = more[0]
            time.sleep(3.5)
            res = h.post("/api/node/key", {"task_id": r["task_id"], "lease_token": r["lease_token"]}, nt)
            self.assertIn(res.get("_status"), (409, 410))
            late = dict(body, task_id=r["task_id"], lease_token=r["lease_token"], idem_key="k-4")
            self.assertEqual(h.post("/api/node/result", late, nt)["status"], "rejected")
            # un gettone falso non serve
            self.assertEqual(h.post("/api/node/work", {}, "falso").get("_status"), 401)
            h.assert_ledger_ok(self)
            self.assertEqual(h.db.execute("SELECT COUNT(*) FROM work_log").fetchone()[0], 1)
        finally:
            h.close()


class T3_Cheater(unittest.TestCase):
    def test_imbroglione_scoperto_dalla_verifica(self):
        h = Hive(verify_p_new=1.0, verify_p_normal=1.0, verify_p_reliable=1.0)
        try:
            req = h.user("richiedente")
            cheat_user = h.user("furbo")
            cheat = h.node(cheat_user, "furbo")
            honest = [h.node(h.user(f"onesto{i}"), f"onesto{i}") for i in range(2)]
            # i nodi onesti si presentano subito, cosi' il server sa che ci sono verificatori
            for r in honest:
                h.post("/api/node/hello", {}, r["node_token"])
            text = make_text(1500, 3)
            job = h.post("/api/jobs", {"name": "con imbroglione", "ops": ["clean", "embed"], "text": text}, req)
            cp = subprocess.Popen(["node", "test/cheater.js", h.url, cheat["node_token"], cheat["seal_public_key"], "6"],
                                  cwd=NODE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            for r in honest:
                h.start_node(r)
            cp.wait(30)
            j = h.wait_job(req, job["id"], 120)
            self.assertEqual(j["status"], "done")
            check_results(self, h, req, job["id"], text)
            c = h.db
            n = c.execute("SELECT cheats, banned FROM nodes WHERE id=?", (cheat["node_id"],)).fetchone()
            self.assertGreater(n["cheats"], 0, "l'imbroglione doveva essere scoperto")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM penalties WHERE node_id!=?", (cheat["node_id"],)).fetchone()[0], 0,
                             "nessun nodo onesto penalizzato")
            h.assert_ledger_ok(self)
        finally:
            h.close()


class T4_ProofOfNine(unittest.TestCase):
    def test_prova_del_nove_e_contestazione(self):
        h = Hive(verify_p_new=0.0)
        try:
            req = h.user("richiedente")
            cheat = h.node(h.user("furbo"), "furbo")
            text = make_text(400, 4)
            job = h.post("/api/jobs", {"name": "da controllare", "ops": ["clean", "embed"], "text": text}, req)
            cp = subprocess.run(["node", "test/cheater.js", h.url, cheat["node_token"], cheat["seal_public_key"], "4"],
                                cwd=NODE_DIR, capture_output=True, timeout=60)
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            j = h.wait_job(req, job["id"], 30)
            self.assertEqual(j["status"], "done")
            # prova del nove del richiedente: rifà la pulizia sui propri originali
            recs = text.split("\n")
            got = [json.loads(x) for x in h.get(f"/api/jobs/{job['id']}/result?embeddings=0", req, raw=True).decode().split("\n") if x]
            wrong = [g["idx"] for g in got if g["duplicate_of"] is None and g["clean"] != run_module("text_clean", recs[g["idx"]].encode()).decode()]
            self.assertTrue(wrong, "il furbo doveva aver alterato qualcosa")
            idx = wrong[0]
            d = h.post(f"/api/jobs/{job['id']}/dispute", {"op": "clean", "idx": idx, "original": recs[idx]}, req)
            self.assertIn("dispute_id", d)
            # un richiedente che contesta un risultato giusto non fa penalizzare nessuno
            ok_idx = next(g["idx"] for g in got if g["idx"] not in wrong and g["duplicate_of"] is None and g["clean"])
            d_ok = h.post(f"/api/jobs/{job['id']}/dispute", {"op": "clean", "idx": ok_idx, "original": recs[ok_idx]}, req)
            for i in range(2):
                h.start_node(h.node(h.user(f"verificatore{i}"), f"v{i}"))
            for _ in range(120):
                if h.get(f"/api/disputes/{d['dispute_id']}", req)["status"] != "open" and \
                        h.get(f"/api/disputes/{d_ok['dispute_id']}", req)["status"] != "open":
                    break
                time.sleep(0.5)
            self.assertEqual(h.get(f"/api/disputes/{d['dispute_id']}", req)["status"], "confirmed")
            self.assertEqual(h.get(f"/api/disputes/{d_ok['dispute_id']}", req)["status"], "rejected")
            fixed = [json.loads(x) for x in h.get(f"/api/jobs/{job['id']}/result?embeddings=0", req, raw=True).decode().split("\n") if x]
            self.assertEqual(fixed[idx]["clean"], run_module("text_clean", recs[idx].encode()).decode(), "risultato non corretto")
            c = h.db
            self.assertGreater(c.execute("SELECT cheats FROM nodes WHERE id=?", (cheat["node_id"],)).fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM penalties").fetchone()[0], 1)
            h.assert_ledger_ok(self)
        finally:
            h.close()


class T6_Security(unittest.TestCase):
    def test_abusi_bloccati(self):
        h = Hive()
        try:
            a = h.user("alice")
            b = h.user("bruno")
            node_a = h.node(a, "ape di alice")
            text = "il gatto di alice dorme sul divano rosso\nla torta di mele della nonna\n" + make_text(300, 6)
            job = h.post("/api/jobs", {"name": "di alice", "ops": ["clean", "embed"], "text": text}, a)
            pk = h.post("/api/node/work", {"max_packets": 1}, node_a["node_token"])["packets"]
            self.assertTrue(pk)
            # 1. bruno non puo' scollegare l'ape di alice ne' rimetterne in coda il lavoro
            r = h.req("DELETE", f"/api/nodes/{node_a['node_id']}", None, b)
            self.assertEqual(r.get("_status"), 404)
            st = h.db.execute("SELECT status, node_id FROM tasks WHERE id=?", (pk[0]["task_id"],)).fetchone()
            self.assertEqual((st["status"], st["node_id"]), ("leased", node_a["node_id"]))
            self.assertEqual(h.db.execute("SELECT revoked FROM nodes WHERE id=?", (node_a["node_id"],)).fetchone()[0], 0)
            h.post("/api/node/release", {"task_ids": [pk[0]["task_id"]]}, node_a["node_token"])
            h.start_node(node_a)
            self.assertEqual(h.wait_job(a, job["id"], 60)["status"], "done")
            # 2. contestazione con un "originale" inventato: rifiutata, nessuna contestazione aperta
            r = h.post(f"/api/jobs/{job['id']}/dispute", {"op": "clean", "idx": 0, "original": "testo inventato"}, a)
            self.assertEqual(r.get("_status"), 409)
            self.assertEqual(h.db.execute("SELECT COUNT(*) FROM disputes").fetchone()[0], 0)
            r = h.post(f"/api/jobs/{job['id']}/dispute", {"op": "clean", "idx": 0, "original": text.split("\n")[0]}, a)
            self.assertIn("dispute_id", r, "con l'originale vero la contestazione e' accettata")
            # 3. la ricerca mostra solo i propri testi
            self.assertTrue(any("torta di mele" in x["text"] for x in h.get("/api/search?q=torta%20di%20mele", a)["results"]))
            self.assertEqual(h.get("/api/search?q=torta%20di%20mele", b)["results"], [])
            # 4. richiesta enorme rifiutata
            big = urllib.request.Request(h.url + "/api/jobs/estimate", data=b"x" * (33 * 1024 * 1024), method="POST",
                                         headers={"content-type": "application/json", "authorization": "Bearer " + a})
            # il server rifiuta prima di leggere: risposta 413 oppure connessione chiusa subito
            with self.assertRaises(urllib.error.URLError) as ctx:
                urllib.request.urlopen(big, timeout=30)
            if isinstance(ctx.exception, urllib.error.HTTPError):
                self.assertEqual(ctx.exception.code, 413)
            self.assertIn("active_bees", h.get("/api/public/stats"), "il server deve restare in piedi")
            # 5. troppi tentativi di accesso: bloccati
            codes = [h.post("/api/auth/login", {"username": "alice", "password": "sbagliata"}).get("_status") for _ in range(12)]
            self.assertIn(429, codes)
        finally:
            h.close()


class T5_Embedding(unittest.TestCase):
    def test_python_uguale_a_webassembly(self):
        from alveare_server.embed_py import embed_record
        texts = ["il gatto dorme sul divano", "àèìòù perché così", "a", "", "x" * 500]
        wasm = np.frombuffer(run_module("embed_hash", "\n".join(texts).encode()), "<f4").reshape(len(texts), 256)
        for t, w in zip(texts, wasm):
            self.assertTrue(np.allclose(embed_record(t.encode()), w, atol=1e-6), t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
