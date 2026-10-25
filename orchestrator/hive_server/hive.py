# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""The heart of the orchestrator: jobs, packets, leases, verification, reuse, honey.

Every write goes through Database.tx(): each operation is all-or-nothing.
Honey amounts are stored in hundredths (100 = 1 honey).
"""
import json
import math
import random
import secrets
import time
import zlib
from datetime import datetime

import numpy as np

from . import ledger, metrics
from .auth import AuthError, USERNAME_RE, check_password, create_session, hash_password, new_token, token_hash
from .config import Settings
from .crypto import ServerKeys, b64d, b64e, encrypt, new_key, sha256_hex
from .db import Database, now
from .embed_py import embed_query
from .modules import EMBED_DIM, OPS, ModuleRegistry, units_for

CENT = 100
ACTIVE_WINDOW_S = 15 * 60
LEVELS = [(0, "Larva"), (50, "Young bee"), (200, "Worker bee"), (500, "Scout bee"),
          (1500, "Forager bee"), (5000, "Guard bee"), (15000, "Master bee")]
MEDALS = {
    "first_comb": ("First comb", "Your bee completed its first cell"),
    "hundred_cells": ("A hundred cells", "100 cells without a single error"),
    "seven_days": ("Seven days", "7 days of work in a row (breaking the streak takes nothing away)"),
    "night_bee": ("Night owl", "20 cells completed at night"),
    "first_request": ("First request", "You submitted your first job to the hive"),
    "shared_memory": ("Shared memory", "One of your jobs reused results the hive already had"),
}
VALID_OPS = ["clean", "stats", "embed"]


class HiveError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _aad(task_id: int, input_sha: str, op: str, version: str) -> bytes:
    return json.dumps({"t": task_id, "h": input_sha, "op": op, "v": version},
                      sort_keys=True, separators=(",", ":")).encode()


def _ids(s: str) -> set:
    return {int(x) for x in s.split(",") if x}


def _join_ids(ids) -> str:
    return "," + ",".join(str(i) for i in sorted(ids)) + "," if ids else ""


def split_records(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


class Hive:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or Settings()
        self.s.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.s.db_path)
        self.keys = ServerKeys(self.s.keys_dir)
        self.modules = ModuleRegistry(self.s.modules_dir, self.keys)
        self._emb_cache = None  # (count, hashes, matrix)
        self.req_stats = metrics.RequestStats()

    # ------------------------------------------------------------------ users
    def register(self, username: str, password: str) -> tuple[int, str]:
        if not USERNAME_RE.match(username or ""):
            raise HiveError("Username: 3-32 characters (letters, digits, dot, hyphen or underscore)")
        if len(password or "") < 8:
            raise HiveError("The password must be at least 8 characters long")
        with self.db.tx() as c:
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise HiveError("This username is already taken", 409)
            uid = c.execute("INSERT INTO users(username, pw_hash, created) VALUES (?,?,?)",
                            (username, hash_password(password), now())).lastrowid
            ledger.post(c, f"welcome:{uid}", "welcome",
                        [("system:mint", -self.s.welcome_honey * CENT), (ledger.user_acct(uid), self.s.welcome_honey * CENT)],
                        "Welcome honey")
        return uid, create_session(self.db, uid)

    def login(self, username: str, password: str) -> tuple[int, str]:
        u = self.db.one("SELECT * FROM users WHERE username=?", (username,))
        if not u or not check_password(password or "", u["pw_hash"]):
            raise HiveError("Incorrect username or password", 401)
        return u["id"], create_session(self.db, u["id"])

    def logout(self, token: str):
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    # ------------------------------------------------------------------ nodes
    def register_node(self, user_id: int, name: str, os_name: str) -> dict:
        name = (name or "My bee").strip()[:40]
        tok = new_token()
        with self.db.tx() as c:
            nid = c.execute("INSERT INTO nodes(user_id, name, os, token_hash, created) VALUES (?,?,?,?,?)",
                            (user_id, name, (os_name or "")[:40], token_hash(tok), now())).lastrowid
        return {"node_id": nid, "node_token": tok, **self.public_keys()}

    def public_keys(self) -> dict:
        return {"seal_public_key": b64e(self.keys.seal_public_raw),
                "sign_public_key": b64e(self.keys.sign_public_raw)}

    def revoke_node(self, user_id: int, node_id: int):
        with self.db.tx() as c:
            own = c.execute("SELECT 1 FROM nodes WHERE id=? AND user_id=? AND revoked=0", (node_id, user_id)).fetchone()
            if not own:
                raise HiveError("Bee not found", 404)   # same answer for other people's bees and missing ones
            c.execute("UPDATE nodes SET revoked=1 WHERE id=? AND user_id=?", (node_id, user_id))
            self._release_node_tasks(c, node_id)

    def node_hello(self, node) -> dict:
        with self.db.tx() as c:
            c.execute("UPDATE nodes SET last_seen=? WHERE id=?", (now(), node["id"]))
        return {"lease_min_s": self.s.lease_min_s, "lease_max_s": self.s.lease_max_s,
                "max_packets": self.s.max_packets_per_request, "node_name": node["name"],
                "banned": bool(node["banned"]), **self.public_keys()}

    # ------------------------------------------------------------------ jobs
    def estimate(self, ops: list[str], text: str) -> dict:
        ops = self._check_ops(ops)
        records = split_records(text)
        nbytes = len(text.encode("utf-8"))
        units = sum(units_for(OPS[op], nbytes) for op in ops)
        return {"records": len(records), "bytes": nbytes, "units": round(units, 2),
                "cost_honey_max": math.ceil(max(1.0, units))}

    def _check_ops(self, ops) -> list[str]:
        ops = [o for o in VALID_OPS if o in set(ops or [])]
        if "clean" not in ops:
            ops = ["clean"] + ops
        return ops

    def submit_job(self, user_id: int, name: str, ops: list[str], text: str) -> int:
        ops = self._check_ops(ops)
        raw = (text or "").encode("utf-8")
        if not raw.strip():
            raise HiveError("The text is empty")
        if len(raw) > self.s.job_max_bytes:
            raise HiveError(f"Text too large (maximum {self.s.job_max_bytes // (1024 * 1024)} MB per job)")
        records = split_records(text)
        est = self.estimate(ops, text)
        reserve = est["cost_honey_max"] * CENT
        with self.db.tx() as c:
            bal = ledger.balance(c, ledger.user_acct(user_id))
            if bal < reserve:
                raise HiveError(f"Not enough honey: up to {reserve / CENT:.2f} is needed, you have {bal / CENT:.2f}", 402)
            jid = c.execute(
                "INSERT INTO jobs(user_id, name, ops, status, created, n_records, reserved) VALUES (?,?,?,?,?,?,?)",
                (user_id, (name or "Job").strip()[:80], ",".join(ops), "phase1", now(), len(records), reserve)).lastrowid
            ledger.post(c, f"reserve:{jid}", "reserve",
                        [(ledger.user_acct(user_id), -reserve), ("system:escrow", reserve)], f"Reserved for job {jid}")
            c.execute("INSERT INTO staging(job_id, blob, expires) VALUES (?,?,?)",
                      (jid, self.keys.seal_at_rest(raw, f"staging:{jid}".encode()), now() + self.s.staging_ttl_s))
            self._make_chunks(c, jid, 1, "clean", records, None)
            self._advance(c, jid)
        return jid

    def _make_chunks(self, c, job_id: int, phase: int, op_name: str, texts: list[str], keys: list[str] | None):
        op = OPS[op_name]
        seq = 0
        start = 0
        n = len(texts)
        while start < n:
            size, end = 0, start
            while end < n and end - start < self.s.chunk_max_records:
                add = len(texts[end].encode("utf-8")) + 1
                if end > start and size + add > self.s.chunk_max_bytes:
                    break
                size += add
                end += 1
            payload = "\n".join(texts[start:end]).encode("utf-8")
            isha = sha256_hex(payload)
            units = units_for(op, len(payload))
            rk = json.dumps(keys[start:end]) if keys is not None else None
            cached = c.execute("SELECT * FROM result_cache WHERE input_sha=? AND op=? AND version=?",
                               (isha, op.name, op.version)).fetchone()
            if cached:
                cid = c.execute(
                    """INSERT INTO chunks(job_id, phase, op, seq, first_record, n_records, input_sha, units, status,
                       result_sha, result_blob, from_cache, record_keys, done_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, phase, op.name, seq, start, end - start, isha, units, "done", cached["result_sha"],
                     cached["result_blob"], 1, rk, now())).lastrowid
                c.execute("UPDATE result_cache SET hits=hits+1 WHERE input_sha=? AND op=? AND version=?",
                          (isha, op.name, op.version))
                c.execute("UPDATE jobs SET units_cached=units_cached+? WHERE id=?", (units, job_id))
            else:
                cid = c.execute(
                    """INSERT INTO chunks(job_id, phase, op, seq, first_record, n_records, input_sha, input_blob, units,
                       status, record_keys) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, phase, op.name, seq, start, end - start, isha, None, units, "queued", rk)).lastrowid
                c.execute("UPDATE chunks SET input_blob=? WHERE id=?",
                          (self.keys.seal_at_rest(payload, f"chunk:{cid}".encode()), cid))
                c.execute("INSERT INTO tasks(chunk_id, kind, status, created) VALUES (?,?,?,?)",
                          (cid, "primary", "queued", now()))
            seq += 1
            start = end

    def _chunk_result(self, ch) -> bytes:
        blob = ch["result_blob"]
        if ch["from_cache"]:
            return zlib.decompress(self.keys.open_at_rest(blob, b"cache"))
        return self.keys.open_at_rest(blob, f"result:{ch['id']}".encode())

    def _advance(self, c, job_id: int):
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job["status"] not in ("phase1", "phase2"):
            return
        phase = 1 if job["status"] == "phase1" else 2
        pending = c.execute("SELECT COUNT(*) FROM chunks WHERE job_id=? AND phase=? AND status!='done'",
                            (job_id, phase)).fetchone()[0]
        if pending:
            return
        ops = job["ops"].split(",")
        if phase == 1:
            st = c.execute("SELECT blob FROM staging WHERE job_id=?", (job_id,)).fetchone()
            originals = split_records(self.keys.open_at_rest(st["blob"], f"staging:{job_id}".encode()).decode("utf-8")) if st else []
            chunks = c.execute("SELECT * FROM chunks WHERE job_id=? AND phase=1 ORDER BY seq", (job_id,)).fetchall()
            cleaned = []
            for ch in chunks:
                part = self._chunk_result(ch).decode("utf-8")
                lines = part.split("\n") if ch["n_records"] else []
                if len(lines) != ch["n_records"]:
                    raise RuntimeError("result has the wrong number of records")
                cleaned.extend(lines)
            first_of = {}
            unique_texts, unique_keys = [], []
            rows = []
            if len(originals) != len(cleaned):
                raise RuntimeError("originals not available for the job")
            for idx, t in enumerate(cleaned):
                oh = sha256_hex(originals[idx].encode("utf-8"))
                if not t:
                    rows.append((job_id, idx, None, None, oh))
                    continue
                h = sha256_hex(t.encode("utf-8"))
                if h in first_of:
                    rows.append((job_id, idx, h, first_of[h], oh))
                    continue
                first_of[h] = idx
                rows.append((job_id, idx, h, None, oh))
                unique_texts.append(t)
                unique_keys.append(h)
                if c.execute("INSERT OR IGNORE INTO items(content_hash, text, created) VALUES (?,?,?)",
                             (h, t, now())).rowcount:
                    c.execute("INSERT INTO items_fts(content_hash, text) VALUES (?,?)", (h, t))
                c.execute("INSERT OR IGNORE INTO item_refs(job_id, content_hash) VALUES (?,?)", (job_id, h))
            c.executemany("INSERT INTO job_records(job_id, idx, content_hash, dup_of, orig_hash) VALUES (?,?,?,?,?)", rows)
            c.execute("UPDATE jobs SET status='phase2', n_unique=? WHERE id=?", (len(unique_texts), job_id))
            if "stats" in ops and unique_texts:
                self._make_chunks(c, job_id, 2, "stats", unique_texts, unique_keys)
            if "embed" in ops:
                ver = OPS["embed"].version
                need = [(t, k) for t, k in zip(unique_texts, unique_keys)
                        if not c.execute("SELECT 1 FROM items WHERE content_hash=? AND embedding IS NOT NULL AND emb_version=?",
                                         (k, ver)).fetchone()]
                need_keys = {k for _, k in need}
                reused_bytes = sum(len(t.encode()) + 1 for t, k in zip(unique_texts, unique_keys) if k not in need_keys)
                if reused_bytes:
                    # reuse at single-text level from the central memory
                    c.execute("UPDATE jobs SET units_cached=units_cached+? WHERE id=?",
                              (units_for(OPS["embed"], reused_bytes), job_id))
                if need:
                    self._make_chunks(c, job_id, 2, "embed", [t for t, _ in need], [k for _, k in need])
            self._advance(c, job_id)
            return
        self._finalize(c, job_id)

    def _finalize(self, c, job_id: int):
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        stats = {"records": 0, "chars": 0, "words": 0, "sentences": 0}
        for ch in c.execute("SELECT * FROM chunks WHERE job_id=? AND phase=2", (job_id,)).fetchall():
            res = self._chunk_result(ch)
            if ch["op"] == "stats":
                r, ch_, w, s = (int(x) for x in res.decode().split())
                stats["records"] += r; stats["chars"] += ch_; stats["words"] += w; stats["sentences"] += s
            elif ch["op"] == "embed":
                keys = json.loads(ch["record_keys"])
                vecs = np.frombuffer(res, dtype="<f4").reshape(len(keys), EMBED_DIM)
                for k, v in zip(keys, vecs):
                    c.execute("UPDATE items SET embedding=?, emb_version=?, emb_node=? WHERE content_hash=?",
                              (v.astype("<f2").tobytes(), OPS["embed"].version, ch["primary_node"], k))
        stats["duplicates"] = job["n_records"] - (job["n_unique"] or 0) - \
            c.execute("SELECT COUNT(*) FROM job_records WHERE job_id=? AND content_hash IS NULL", (job_id,)).fetchone()[0]
        stats["empty"] = c.execute("SELECT COUNT(*) FROM job_records WHERE job_id=? AND content_hash IS NULL",
                                   (job_id,)).fetchone()[0]
        # clean-up: originals and comparison data are no longer needed
        c.execute("DELETE FROM staging WHERE job_id=?", (job_id,))
        c.execute("UPDATE chunks SET input_blob=NULL, result_blob=NULL WHERE job_id=?", (job_id,))
        cost = math.ceil((job["units_distributed"] + job["units_cached"] * self.s.cached_cost_factor) * CENT)
        cost = max(1 * CENT // 10, min(cost, job["reserved"]))
        uacct = ledger.user_acct(job["user_id"])
        ledger.post(c, f"settle:{job_id}", "job",
                    [("system:escrow", -job["reserved"]), ("system:spend", cost), (uacct, job["reserved"] - cost)],
                    f"Job {job_id}: {job['name']}")
        c.execute("UPDATE jobs SET status='done', finished=?, stats=?, cost=? WHERE id=?",
                  (now(), json.dumps(stats), cost, job_id))
        self._emb_cache = None

    def _fail_job(self, c, job_id: int, reason: str):
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job["status"] in ("done", "failed"):
            return
        c.execute("DELETE FROM staging WHERE job_id=?", (job_id,))
        c.execute("UPDATE chunks SET input_blob=NULL, result_blob=NULL WHERE job_id=?", (job_id,))
        c.execute("""UPDATE tasks SET status='cancelled' WHERE status IN ('queued','leased')
                     AND chunk_id IN (SELECT id FROM chunks WHERE job_id=?)""", (job_id,))
        ledger.post(c, f"settle:{job_id}", "refund",
                    [("system:escrow", -job["reserved"]), (ledger.user_acct(job["user_id"]), job["reserved"])],
                    f"Job {job_id} not completed: refund")
        c.execute("UPDATE jobs SET status='failed', finished=?, stats=? WHERE id=?",
                  (now(), json.dumps({"error": reason}), job_id))

    # ------------------------------------------------------------------ node protocol
    def get_work(self, node, max_packets: int = 1, proposed_lease_s: float | None = None) -> list[dict]:
        max_packets = max(1, min(int(max_packets or 1), self.s.max_packets_per_request))
        lease_s = proposed_lease_s or self.s.lease_default_s
        lease_s = max(self.s.lease_min_s, min(self.s.lease_max_s, float(lease_s)))
        out = []
        with self.db.tx() as c:
            c.execute("UPDATE nodes SET last_seen=? WHERE id=?", (now(), node["id"]))
            if node["banned"] or node["revoked"]:
                return []
            cand = c.execute(
                """SELECT t.id, t.kind, t.exclude_nodes, t.exclude_users, c.job_id, c.id AS chunk_id
                   FROM tasks t JOIN chunks c ON c.id=t.chunk_id
                   WHERE t.status='queued' ORDER BY t.created LIMIT 400""").fetchall()
            by_job = {}
            for r in cand:
                if node["id"] in _ids(r["exclude_nodes"]) or node["user_id"] in _ids(r["exclude_users"]):
                    continue
                by_job.setdefault(r["job_id"], []).append(r)
            # shuffling: one packet per job per round, picked at random within the job
            picked = []
            jobs = list(by_job)
            random.shuffle(jobs)
            while len(picked) < max_packets and jobs:
                for j in list(jobs):
                    lst = by_job[j]
                    if not lst:
                        jobs.remove(j)
                        continue
                    picked.append(lst.pop(random.randrange(len(lst))))
                    if len(picked) >= max_packets:
                        break
            for r in picked:
                t = c.execute("SELECT * FROM tasks WHERE id=?", (r["id"],)).fetchone()
                ch = c.execute("SELECT * FROM chunks WHERE id=?", (t["chunk_id"],)).fetchone()
                op = OPS[ch["op"]]
                if t["input_blob"] is not None:
                    plain = self.keys.open_at_rest(t["input_blob"], f"task:{t['id']}".encode())
                    isha = sha256_hex(plain)
                else:
                    if ch["input_blob"] is None:
                        c.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (t["id"],))
                        continue
                    plain = self.keys.open_at_rest(ch["input_blob"], f"chunk:{ch['id']}".encode())
                    isha = ch["input_sha"]
                key = new_key()
                aad = _aad(t["id"], isha, op.name, op.version)
                nonce, ct = encrypt(key, plain, aad)
                lease = secrets.token_urlsafe(24)
                deadline = now() + lease_s
                c.execute("""UPDATE tasks SET status='leased', node_id=?, lease_token=?, lease_deadline=?, packet_key=?,
                             leased_at=? WHERE id=?""", (node["id"], lease, deadline, key, now(), t["id"]))
                if t["kind"] == "primary":
                    c.execute("UPDATE chunks SET status='running' WHERE id=?", (ch["id"],))
                out.append({"task_id": t["id"], "lease_token": lease, "lease_seconds": lease_s,
                            "op": op.name, "module": op.module, "version": op.version,
                            "units": round(ch["units"], 4), "aad": b64e(aad), "nonce": b64e(nonce), "ct": b64e(ct)})
        return out

    def retry_after(self) -> int:
        """When a node gets no work: how long it should wait before asking again (seconds)."""
        busy = self.db.one("SELECT COUNT(*) FROM jobs WHERE status IN ('phase1','phase2')")[0]
        return 10 if busy else 60

    def get_key(self, node, task_id: int, lease_token: str) -> str:
        t = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if (not t or t["status"] != "leased" or t["node_id"] != node["id"]
                or not secrets.compare_digest(t["lease_token"] or "", lease_token or "")):
            raise HiveError("Invalid lease", 409)
        if t["lease_deadline"] < now():
            raise HiveError("Lease expired", 410)
        return b64e(t["packet_key"])

    def release(self, node, task_ids: list[int]):
        with self.db.tx() as c:
            for tid in task_ids or []:
                t = c.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
                if t and t["status"] == "leased" and t["node_id"] == node["id"]:
                    self._requeue(c, t, penalize=False)

    def _release_node_tasks(self, c, node_id: int):
        for t in c.execute("SELECT * FROM tasks WHERE status='leased' AND node_id=?", (node_id,)).fetchall():
            self._requeue(c, t, penalize=False)

    def _requeue(self, c, t, penalize: bool):
        attempts = t["attempts"] + (1 if penalize else 0)
        if penalize and t["node_id"]:
            c.execute("UPDATE nodes SET missed=missed+1 WHERE id=?", (t["node_id"],))
        ch = c.execute("SELECT * FROM chunks WHERE id=?", (t["chunk_id"],)).fetchone()
        if attempts >= self.s.max_attempts:
            c.execute("UPDATE tasks SET status='dead', attempts=?, packet_key=NULL WHERE id=?", (attempts, t["id"]))
            if t["kind"] == "primary":
                c.execute("UPDATE chunks SET status='dead' WHERE id=?", (ch["id"],))
                self._fail_job(c, ch["job_id"], "A packet failed too many times")
            return
        c.execute("""UPDATE tasks SET status='queued', node_id=NULL, lease_token=NULL, lease_deadline=NULL,
                     packet_key=NULL, attempts=? WHERE id=?""", (attempts, t["id"]))
        if t["kind"] == "primary":
            c.execute("UPDATE chunks SET status='queued' WHERE id=?", (ch["id"],))

    def _validate_output(self, op: str, n_records: int, data: bytes) -> bool:
        if op == "clean":
            try:
                txt = data.decode("utf-8")
            except UnicodeDecodeError:
                return False
            return (len(txt.split("\n")) if n_records else 0) == n_records or (n_records == 0 and txt == "")
        if op == "stats":
            parts = data.decode("ascii", "replace").split()
            return len(parts) == 4 and all(p.isdigit() for p in parts)
        if op == "embed":
            return len(data) == n_records * EMBED_DIM * 4
        return False

    def _node_reliability(self, n) -> float:
        return (n["accepted"] + 1) / (n["accepted"] + n["missed"] * 0.5 + n["rejected"] * 2 + n["cheats"] * 10 + 2)

    def submit_result(self, node, task_id: int, lease_token: str, idem_key: str, sealed: dict, result_sha: str,
                      node_metrics: dict | None = None) -> dict:
        t0 = time.perf_counter()
        with self.db.tx() as c:
            before = c.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone()
            res = self._submit_result(c, node, task_id, lease_token, idem_key, sealed, result_sha)
            # The metric is stored in the SAME transaction as the result: both or neither.
            # Only for a valid lease held by this node (not for duplicates or forged leases).
            if (before is not None and before["node_id"] == node["id"] and before["status"] == "leased"
                    and res.get("status") in ("accepted", "rejected")):
                metrics.record(c, before, node, res["status"], (time.perf_counter() - t0) * 1000, sealed, node_metrics)
        return res

    def _submit_result(self, c, node, task_id: int, lease_token: str, idem_key: str, sealed: dict, result_sha: str) -> dict:
        c.execute("UPDATE nodes SET last_seen=? WHERE id=?", (now(), node["id"]))
        if not idem_key or len(idem_key) > 100:
            raise HiveError("Missing delivery key")
        if c.execute("SELECT 1 FROM delivered WHERE idem_key=?", (idem_key,)).fetchone():
            return {"status": "duplicate"}
        t = c.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone()
        if (not t or t["status"] != "leased" or t["node_id"] != node["id"]
                or not secrets.compare_digest(t["lease_token"] or "", lease_token or "")):
            return {"status": "rejected", "reason": "invalid lease"}
        if t["lease_deadline"] < now():
            self._requeue(c, t, penalize=True)
            return {"status": "rejected", "reason": "lease expired"}
        ch = c.execute("SELECT * FROM chunks WHERE id=?", (t["chunk_id"],)).fetchone()
        op = OPS[ch["op"]]
        if t["input_blob"] is not None:
            isha = sha256_hex(self.keys.open_at_rest(t["input_blob"], f"task:{t['id']}".encode()))
            n_rec = len(self.keys.open_at_rest(t["input_blob"], f"task:{t['id']}".encode()).split(b"\n"))
        else:
            isha, n_rec = ch["input_sha"], ch["n_records"]
        aad = _aad(t["id"], isha, op.name, op.version)
        try:
            data = self.keys.open_sealed(b64d(sealed["epk"]), b64d(sealed["nonce"]), b64d(sealed["ct"]), aad)
        except Exception:
            c.execute("UPDATE nodes SET rejected=rejected+1 WHERE id=?", (node["id"],))
            self._requeue(c, t, penalize=False)
            return {"status": "rejected", "reason": "result could not be decrypted"}
        sha = sha256_hex(data)
        if sha != result_sha or not self._validate_output(op.name, n_rec, data):
            c.execute("UPDATE nodes SET rejected=rejected+1 WHERE id=?", (node["id"],))
            self._requeue(c, t, penalize=False)
            return {"status": "rejected", "reason": "invalid result"}
        c.execute("INSERT INTO delivered(idem_key, task_id, at) VALUES (?,?,?)", (idem_key, t["id"], now()))
        c.execute("""UPDATE tasks SET status='done', finished=?, result_sha=?, packet_key=NULL, lease_token=NULL
                     WHERE id=?""", (now(), sha, t["id"]))
        c.execute("UPDATE nodes SET accepted=accepted+1 WHERE id=?", (node["id"],))
        n = c.execute("SELECT * FROM nodes WHERE id=?", (node["id"],)).fetchone()
        honey = self._award(c, n, t, ch, n_rec)
        if t["kind"] == "primary":
            c.execute("UPDATE jobs SET units_distributed=units_distributed+? WHERE id=?", (ch["units"], ch["job_id"]))
            c.execute("UPDATE chunks SET result_sha=?, result_blob=?, primary_node=? WHERE id=?",
                      (sha, self.keys.seal_at_rest(data, f"result:{ch['id']}".encode()), node["id"], ch["id"]))
            if self._should_verify(c, n):
                c.execute("UPDATE chunks SET status='verifying' WHERE id=?", (ch["id"],))
                c.execute("""INSERT INTO tasks(chunk_id, kind, status, created, exclude_nodes, exclude_users)
                             VALUES (?,?,?,?,?,?)""",
                          (ch["id"], "verify", "queued", now(), _join_ids({node["id"]}), _join_ids({node["user_id"]})))
            else:
                self._chunk_done(c, ch["id"], data)
        elif t["kind"] == "verify":
            c.execute("UPDATE tasks SET result_blob=? WHERE id=?",
                      (self.keys.seal_at_rest(data, f"vres:{t['id']}".encode()), t["id"]))
            self._resolve_verification(c, ch["id"])
        elif t["kind"] == "dispute":
            c.execute("UPDATE tasks SET result_blob=? WHERE id=?",
                      (self.keys.seal_at_rest(data, f"vres:{t['id']}".encode()), t["id"]))
            self._resolve_dispute(c, t["dispute_id"])
        return {"status": "accepted", "honey": honey / CENT}

    def _should_verify(self, c, n) -> bool:
        others = c.execute("""SELECT COUNT(*) FROM nodes WHERE id!=? AND user_id!=? AND banned=0 AND revoked=0
                              AND last_seen>?""", (n["id"], n["user_id"], now() - ACTIVE_WINDOW_S)).fetchone()[0]
        if not others:
            return False
        if n["accepted"] < 20:
            p = self.s.verify_p_new
        elif n["accepted"] >= 50 and self._node_reliability(n) > 0.9:
            p = self.s.verify_p_reliable
        else:
            p = self.s.verify_p_normal
        return random.random() < p

    def _chunk_done(self, c, chunk_id: int, data: bytes):
        ch = c.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        op = OPS[ch["op"]]
        c.execute("UPDATE chunks SET status='done', done_at=? WHERE id=?", (now(), chunk_id))
        c.execute("""INSERT OR IGNORE INTO result_cache(input_sha, op, version, result_blob, result_sha, created)
                     VALUES (?,?,?,?,?,?)""",
                  (ch["input_sha"], op.name, op.version, self.keys.seal_at_rest(zlib.compress(data), b"cache"),
                   sha256_hex(data), now()))
        self._advance(c, ch["job_id"])

    def _penalize(self, c, node_id: int, reason: str, evidence: dict, units: float):
        n = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not n:
            return
        amount = max(CENT, int(units * CENT * 3))
        pid = c.execute("""INSERT INTO penalties(node_id, user_id, reason, evidence, amount, created)
                           VALUES (?,?,?,?,?,?)""", (node_id, n["user_id"], reason, json.dumps(evidence), amount, now())).lastrowid
        ledger.post(c, f"penalty:{pid}", "penalty",
                    [(ledger.user_acct(n["user_id"]), -amount), ("system:penalty", amount)], reason)
        cheats = n["cheats"] + 1
        c.execute("UPDATE nodes SET cheats=?, banned=? WHERE id=?", (cheats, 1 if cheats >= 3 else 0, node_id))

    def _resolve_verification(self, c, chunk_id: int):
        ch = c.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        vts = c.execute("SELECT * FROM tasks WHERE chunk_id=? AND kind='verify' AND status='done' ORDER BY id",
                        (chunk_id,)).fetchall()
        primary_sha = ch["result_sha"]
        if len(vts) == 1:
            v = vts[0]
            if v["result_sha"] == primary_sha:
                self._chunk_done(c, chunk_id, self.keys.open_at_rest(ch["result_blob"], f"result:{chunk_id}".encode()))
            else:
                # disagreement: a third node has to decide
                c.execute("""INSERT INTO tasks(chunk_id, kind, status, created, exclude_nodes, exclude_users)
                             VALUES (?,?,?,?,?,?)""",
                          (chunk_id, "verify", "queued", now(),
                           _join_ids({ch["primary_node"], v["node_id"]}),
                           _join_ids({self._user_of(c, ch["primary_node"]), self._user_of(c, v["node_id"])})))
            return
        if len(vts) >= 2:
            v1, v2 = vts[0], vts[1]
            evidence = {"chunk": chunk_id, "primary": [ch["primary_node"], primary_sha],
                        "verify": [[v1["node_id"], v1["result_sha"]], [v2["node_id"], v2["result_sha"]]]}
            if v2["result_sha"] == primary_sha:
                self._penalize(c, v1["node_id"], "False verification result", evidence, ch["units"])
                self._chunk_done(c, chunk_id, self.keys.open_at_rest(ch["result_blob"], f"result:{chunk_id}".encode()))
            elif v2["result_sha"] == v1["result_sha"]:
                self._penalize(c, ch["primary_node"], "False result caught by verification", evidence, ch["units"])
                good = self.keys.open_at_rest(v2["result_blob"], f"vres:{v2['id']}".encode())
                c.execute("UPDATE chunks SET result_sha=?, result_blob=? WHERE id=?",
                          (v2["result_sha"], self.keys.seal_at_rest(good, f"result:{chunk_id}".encode()), chunk_id))
                self._chunk_done(c, chunk_id, good)
            else:
                # three different results: start over
                c.execute("UPDATE chunks SET status='queued', result_sha=NULL, result_blob=NULL WHERE id=?", (chunk_id,))
                c.execute("INSERT INTO tasks(chunk_id, kind, status, created) VALUES (?,?,?,?)",
                          (chunk_id, "primary", "queued", now()))

    def _user_of(self, c, node_id):
        r = c.execute("SELECT user_id FROM nodes WHERE id=?", (node_id,)).fetchone()
        return r["user_id"] if r else 0

    def _award(self, c, n, t, ch, n_records: int) -> int:
        reward = ch["units"] * CENT
        if n["accepted"] >= 50 and self._node_reliability(n) >= 0.95:
            reward *= 1.2
        backlog = c.execute("SELECT COUNT(*) FROM tasks WHERE status='queued'").fetchone()[0]
        if backlog > 100:
            reward *= 1.1
        reward = max(1, int(round(reward)))
        user = c.execute("SELECT * FROM users WHERE id=?", (n["user_id"],)).fetchone()
        total_acc = c.execute("SELECT COALESCE(SUM(accepted),0) FROM nodes WHERE user_id=?", (n["user_id"],)).fetchone()[0]
        is_new = (now() - user["created"] < 7 * 86400) or total_acc < 50
        cap = (self.s.daily_cap_new if is_new else self.s.daily_cap) * CENT
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = c.execute("SELECT COALESCE(SUM(honey),0) FROM work_log WHERE user_id=? AND at>=?",
                          (n["user_id"], day_start)).fetchone()[0]
        reward = max(0, min(reward, cap - today))
        if reward:
            ledger.post(c, f"work:{t['id']}", "bee work",
                        [("system:mint", -reward), (ledger.user_acct(n["user_id"]), reward)], f"Cell {t['id']}")
        c.execute("""INSERT INTO work_log(node_id, user_id, task_id, job_id, records, units, honey, at)
                     VALUES (?,?,?,?,?,?,?,?)""", (n["id"], n["user_id"], t["id"], ch["job_id"], n_records,
                                                  ch["units"], reward, now()))
        return reward

    # ------------------------------------------------------------------ spot checks and disputes
    def job_sample(self, user_id: int, job_id: int, n: int = 20) -> dict:
        job = self._own_job(user_id, job_id)
        if job["status"] != "done":
            raise HiveError("The job has not finished yet")
        rows = self.db.q("""SELECT r.idx, r.content_hash, i.text, i.embedding FROM job_records r
                            JOIN items i ON i.content_hash=r.content_hash
                            WHERE r.job_id=? AND r.dup_of IS NULL""", (job_id,))
        rows = random.sample(list(rows), min(n, len(rows)))
        return {"records": [{"idx": r["idx"], "clean": r["text"],
                             "embedding_f16": b64e(r["embedding"]) if r["embedding"] else None} for r in rows]}

    def open_dispute(self, user_id: int, job_id: int, op: str, idx: int, original: str | None = None) -> int:
        job = self._own_job(user_id, job_id)
        if op not in ("clean", "embed"):
            raise HiveError("Only cleaning or embedding results can be disputed")
        rec = self.db.one("""SELECT r.*, i.text, i.embedding, i.emb_node FROM job_records r
                             JOIN items i ON i.content_hash=r.content_hash WHERE r.job_id=? AND r.idx=?""", (job_id, idx))
        if not rec:
            raise HiveError("Record not found", 404)
        with self.db.tx() as c:
            if op == "clean":
                if original is None:
                    raise HiveError("To dispute a cleaning result the original text of the record is required")
                inp = original.encode("utf-8")
                # the original must be exactly the one submitted: it is checked against its hash
                if "\n" in original or not rec["orig_hash"] or sha256_hex(inp) != rec["orig_hash"]:
                    raise HiveError("The original text does not match the one submitted with the job", 409)
                expected = rec["text"].encode("utf-8")
                ch = c.execute("""SELECT * FROM chunks WHERE job_id=? AND phase=1 AND first_record<=?
                                  AND first_record+n_records>? """, (job_id, idx, idx)).fetchone()
                suspect = ch["primary_node"] if ch else None
            else:
                inp = rec["text"].encode("utf-8")
                expected = rec["embedding"] or b""
                ch = c.execute("SELECT * FROM chunks WHERE job_id=? AND op='embed' LIMIT 1", (job_id,)).fetchone()
                suspect = rec["emb_node"]
            if ch is None:
                ch = c.execute("SELECT * FROM chunks WHERE job_id=? LIMIT 1", (job_id,)).fetchone()
            did = c.execute("""INSERT INTO disputes(job_id, user_id, op, chunk_id, expected_sha, status, created, outcome)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (job_id, user_id, op, ch["id"], sha256_hex(expected), "open", now(),
                             json.dumps({"idx": idx, "suspect": suspect}))).lastrowid
            # the dispute packet has its own input: a dedicated chunk is created for it
            dch = c.execute("""INSERT INTO chunks(job_id, phase, op, seq, first_record, n_records, input_sha, units,
                               status, record_keys) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (job_id, 9, op, idx, idx, 1, sha256_hex(inp), units_for(OPS[op], len(inp)), "dispute",
                             json.dumps([rec["content_hash"]]))).lastrowid
            excl_n = {suspect} if suspect else set()
            excl_u = {self._user_of(c, suspect)} if suspect else set()
            excl_u.add(user_id)
            for _ in range(2):
                tid = c.execute("""INSERT INTO tasks(chunk_id, kind, status, created, exclude_nodes, exclude_users,
                                   dispute_id) VALUES (?,?,?,?,?,?,?)""",
                                (dch, "dispute", "queued", now(), _join_ids(excl_n), _join_ids(excl_u), did)).lastrowid
                c.execute("UPDATE tasks SET input_blob=? WHERE id=?",
                          (self.keys.seal_at_rest(inp, f"task:{tid}".encode()), tid))
            return did

    def _resolve_dispute(self, c, dispute_id: int):
        d = c.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        ts = c.execute("SELECT * FROM tasks WHERE dispute_id=? AND status='done' ORDER BY id", (dispute_id,)).fetchall()
        if len(ts) < 2 or d["status"] != "open":
            return
        a = self.keys.open_at_rest(ts[0]["result_blob"], f"vres:{ts[0]['id']}".encode())
        b = self.keys.open_at_rest(ts[1]["result_blob"], f"vres:{ts[1]['id']}".encode())
        if d["op"] == "embed":
            a = np.frombuffer(a, "<f4").astype("<f2").tobytes()
            b = np.frombuffer(b, "<f4").astype("<f2").tobytes()
        info = json.loads(d["outcome"])
        if a != b:
            status, info["result"] = "rejected", "verifiers disagree: no penalty"
        elif sha256_hex(a) == d["expected_sha"]:
            status, info["result"] = "rejected", "the recorded result was correct"
        else:
            status, info["result"] = "confirmed", "the recorded result was wrong and has been corrected"
            rec = c.execute("SELECT * FROM job_records WHERE job_id=? AND idx=?", (d["job_id"], info["idx"])).fetchone()
            if d["op"] == "embed":
                c.execute("UPDATE items SET embedding=? WHERE content_hash=?", (a, rec["content_hash"]))
            else:
                t = a.decode("utf-8")
                h = sha256_hex(a)
                if c.execute("INSERT OR IGNORE INTO items(content_hash, text, created) VALUES (?,?,?)",
                             (h, t, now())).rowcount:
                    c.execute("INSERT INTO items_fts(content_hash, text) VALUES (?,?)", (h, t))
                c.execute("INSERT OR IGNORE INTO item_refs(job_id, content_hash) VALUES (?,?)", (d["job_id"], h))
                c.execute("UPDATE job_records SET content_hash=? WHERE job_id=? AND idx=?", (h, d["job_id"], info["idx"]))
            if info.get("suspect"):
                self._penalize(c, info["suspect"], "False result confirmed by a spot check",
                               {"dispute": dispute_id, "expected": d["expected_sha"], "verified": sha256_hex(a)}, 1.0)
            self._emb_cache = None
        c.execute("UPDATE disputes SET status=?, outcome=? WHERE id=?", (status, json.dumps(info), dispute_id))

    def dispute_status(self, user_id: int, dispute_id: int) -> dict:
        d = self.db.one("SELECT * FROM disputes WHERE id=? AND user_id=?", (dispute_id, user_id))
        if not d:
            raise HiveError("Dispute not found", 404)
        return {"id": d["id"], "status": d["status"], "op": d["op"], "outcome": json.loads(d["outcome"] or "{}")}

    # ------------------------------------------------------------------ maintenance (expired leases etc.)
    def maintenance(self):
        t0 = now()
        with self.db.tx() as c:
            for t in c.execute("SELECT * FROM tasks WHERE status='leased' AND lease_deadline<?", (t0,)).fetchall():
                self._requeue(c, t, penalize=True)
            # verifications no other node picks up: the first result is accepted
            for t in c.execute("""SELECT t.* FROM tasks t JOIN chunks ch ON ch.id=t.chunk_id
                                  WHERE t.kind='verify' AND t.status='queued' AND t.created<? AND ch.status='verifying'""",
                               (t0 - self.s.verify_wait_s,)).fetchall():
                c.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (t["id"],))
                ch = c.execute("SELECT * FROM chunks WHERE id=?", (t["chunk_id"],)).fetchone()
                self._chunk_done(c, ch["id"], self.keys.open_at_rest(ch["result_blob"], f"result:{ch['id']}".encode()))
            for s in c.execute("SELECT job_id FROM staging WHERE expires<?", (t0,)).fetchall():
                self._fail_job(c, s["job_id"], "Maximum retention time exceeded")
            c.execute("DELETE FROM delivered WHERE at<?", (t0 - 7 * 86400,))

    # ------------------------------------------------------------------ results and memory
    def _own_job(self, user_id: int, job_id: int):
        job = self.db.one("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id))
        if not job:
            raise HiveError("Job not found", 404)
        return job

    def job_info(self, user_id: int, job_id: int) -> dict:
        j = self._own_job(user_id, job_id)
        tot = self.db.one("SELECT COUNT(*) n, SUM(status='done') d FROM chunks WHERE job_id=? AND phase IN (1,2)", (job_id,))
        return {"id": j["id"], "name": j["name"], "ops": j["ops"].split(","), "status": j["status"],
                "created": j["created"], "finished": j["finished"], "records": j["n_records"], "unique": j["n_unique"],
                "stats": json.loads(j["stats"]) if j["stats"] else None,
                "cells_total": tot["n"] or 0, "cells_done": tot["d"] or 0,
                "reserved": j["reserved"] / CENT, "cost": (j["cost"] or 0) / CENT,
                "reused": j["units_cached"] > 0, "memory_deleted": bool(j["memory_deleted"]),
                "live": metrics.job_live(self.db, j)}

    def job_metrics(self, user_id: int, job_id: int) -> dict:
        return metrics.job_metrics(self.db, self._own_job(user_id, job_id), self.req_stats)

    def job_report(self, user_id: int, job_id: int) -> str:
        return metrics.job_report_md(self.job_metrics(user_id, job_id))

    def job_csv(self, user_id: int, job_id: int) -> str:
        self._own_job(user_id, job_id)
        return metrics.job_csv(self.db, job_id)

    def list_jobs(self, user_id: int) -> list[dict]:
        return [self.job_info(user_id, r["id"]) for r in
                self.db.q("SELECT id FROM jobs WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))]

    def job_results(self, user_id: int, job_id: int, with_embeddings: bool = True):
        j = self._own_job(user_id, job_id)
        if j["status"] != "done":
            raise HiveError("The job has not finished yet")
        rows = self.db.q("""SELECT r.idx, r.dup_of, i.text, i.embedding FROM job_records r
                            LEFT JOIN items i ON i.content_hash=r.content_hash WHERE r.job_id=? ORDER BY r.idx""", (job_id,))
        for r in rows:
            rec = {"idx": r["idx"], "clean": r["text"] or "", "duplicate_of": r["dup_of"]}
            if with_embeddings and r["embedding"] is not None:
                rec["embedding"] = [round(float(x), 5) for x in np.frombuffer(r["embedding"], "<f2")]
            yield json.dumps(rec, ensure_ascii=False) + "\n"

    def delete_job_memory(self, user_id: int, job_id: int) -> int:
        """Deletion on request: removes the job references and any texts not used by other jobs."""
        self._own_job(user_id, job_id)
        with self.db.tx() as c:
            hashes = [r[0] for r in c.execute("SELECT content_hash FROM item_refs WHERE job_id=?", (job_id,))]
            c.execute("DELETE FROM item_refs WHERE job_id=?", (job_id,))
            removed = 0
            for h in hashes:
                if not c.execute("SELECT 1 FROM item_refs WHERE content_hash=?", (h,)).fetchone():
                    c.execute("DELETE FROM items WHERE content_hash=?", (h,))
                    c.execute("DELETE FROM items_fts WHERE content_hash=?", (h,))
                    removed += 1
            c.execute("UPDATE job_records SET content_hash=NULL WHERE job_id=?", (job_id,))
            c.execute("UPDATE jobs SET memory_deleted=1 WHERE id=?", (job_id,))
        self._emb_cache = None
        return removed

    def search(self, user_id: int, q: str, k: int = 10) -> list[dict]:
        """Searches ONLY the texts of the user's own jobs: the shared memory exists for reuse,
        not to show one person's texts to anyone else."""
        q = (q or "").strip()
        if not q:
            return []
        mine = {r[0] for r in self.db.q("""SELECT DISTINCT r.content_hash FROM item_refs r
                                          JOIN jobs j ON j.id=r.job_id WHERE j.user_id=?""", (user_id,))}
        if not mine:
            return []
        k = max(1, min(int(k), 50))
        count = self.db.one("SELECT COUNT(*) FROM items WHERE embedding IS NOT NULL")[0]
        if self._emb_cache is None or self._emb_cache[0] != count:
            rows = self.db.q("SELECT content_hash, embedding FROM items WHERE embedding IS NOT NULL")
            hashes = [r[0] for r in rows]
            mat = (np.frombuffer(b"".join(r[1] for r in rows), "<f2").reshape(len(rows), EMBED_DIM).astype(np.float32)
                   if rows else np.zeros((0, EMBED_DIM), np.float32))
            self._emb_cache = (count, hashes, mat)
        _, hashes, mat = self._emb_cache
        scores = {}
        if len(hashes):
            sims = mat @ embed_query(q)
            for i in np.argsort(-sims):
                if hashes[i] in mine:
                    scores[hashes[i]] = float(sims[i])
                    if len(scores) >= k * 3:
                        break
        words = [w for w in "".join(ch if ch.isalnum() else " " for ch in q.lower()).split() if len(w) > 1]
        if words:
            fts_q = " AND ".join(f'"{w}"' for w in words[:8])
            try:
                for r in self.db.q("SELECT content_hash FROM items_fts WHERE items_fts MATCH ? LIMIT 2000", (fts_q,)):
                    if r[0] in mine:
                        scores[r[0]] = scores.get(r[0], 0.0) + 0.3
            except Exception:
                pass
        best = sorted(scores.items(), key=lambda x: -x[1])[:k]
        out = []
        with self.db.tx() as c:
            for h, sc in best:
                r = c.execute("SELECT text FROM items WHERE content_hash=?", (h,)).fetchone()
                if r:
                    c.execute("UPDATE items SET hits=hits+1, last_access=? WHERE content_hash=?", (now(), h))
                    out.append({"text": r["text"][:500], "score": round(sc, 3)})
        return out

    # ------------------------------------------------------------------ game, groups and statistics
    def me(self, user) -> dict:
        uid = user["id"]
        c = self.db.connect()
        bal = ledger.balance(c, ledger.user_acct(uid))
        earned = c.execute("SELECT COALESCE(SUM(honey),0) FROM work_log WHERE user_id=?", (uid,)).fetchone()[0] / CENT
        level_i = max(i for i, (th, _) in enumerate(LEVELS) if earned >= th)
        nxt = LEVELS[level_i + 1][0] if level_i + 1 < len(LEVELS) else None
        week = now() - 7 * 86400
        wk = c.execute("""SELECT COUNT(*) cells, COALESCE(SUM(records),0) recs, COUNT(DISTINCT job_id) jobs,
                          COALESCE(SUM(honey),0) honey FROM work_log WHERE user_id=? AND at>?""", (uid, week)).fetchone()
        nodes = [{"id": n["id"], "name": n["name"], "os": n["os"], "accepted": n["accepted"],
                  "active": bool(n["last_seen"] and n["last_seen"] > now() - ACTIVE_WINDOW_S),
                  "reliability": round(self._node_reliability(n), 3), "banned": bool(n["banned"])}
                 for n in c.execute("SELECT * FROM nodes WHERE user_id=? AND revoked=0", (uid,))]
        return {"username": user["username"], "honey": bal / CENT, "earned_total": earned,
                "level": {"index": level_i, "name": LEVELS[level_i][1], "next_at": nxt},
                "medals": self.medals(uid), "week": {"cells": wk["cells"], "records": wk["recs"], "jobs_helped": wk["jobs"],
                                                     "honey": wk["honey"] / CENT},
                "nodes": nodes, "group": self.my_group(uid)}

    def medals(self, uid: int) -> list[dict]:
        c = self.db.connect()
        got = set()
        n_work = c.execute("SELECT COUNT(*) FROM work_log WHERE user_id=?", (uid,)).fetchone()[0]
        if n_work >= 1:
            got.add("first_comb")
        pens = c.execute("SELECT COUNT(*) FROM penalties WHERE user_id=?", (uid,)).fetchone()[0]
        if n_work >= 100 and pens == 0:
            got.add("hundred_cells")
        days = sorted({datetime.fromtimestamp(r[0]).date() for r in c.execute("SELECT at FROM work_log WHERE user_id=?", (uid,))})
        best = run = 0
        prev = None
        for d in days:
            run = run + 1 if prev and (d - prev).days == 1 else 1
            best = max(best, run)
            prev = d
        if best >= 7:
            got.add("seven_days")
        night = sum(1 for r in c.execute("SELECT at FROM work_log WHERE user_id=?", (uid,))
                    if datetime.fromtimestamp(r[0]).hour < 6)
        if night >= 20:
            got.add("night_bee")
        if c.execute("SELECT 1 FROM jobs WHERE user_id=?", (uid,)).fetchone():
            got.add("first_request")
        if c.execute("SELECT 1 FROM jobs WHERE user_id=? AND units_cached>0", (uid,)).fetchone():
            got.add("shared_memory")
        return [{"id": k, "name": v[0], "desc": v[1], "earned": k in got} for k, v in MEDALS.items()]

    def ledger_history(self, uid: int) -> list[dict]:
        return [{**h, "amount": h["amount"] / CENT} for h in ledger.history(self.db.connect(), ledger.user_acct(uid))]

    def create_group(self, uid: int, name: str) -> dict:
        name = (name or "").strip()[:40]
        if len(name) < 2:
            raise HiveError("Please give the group a name")
        code = secrets.token_hex(3).upper()
        with self.db.tx() as c:
            c.execute("DELETE FROM group_members WHERE user_id=?", (uid,))
            gid = c.execute("INSERT INTO groups_(name, code, created) VALUES (?,?,?)", (name, code, now())).lastrowid
            c.execute("INSERT INTO group_members(group_id, user_id) VALUES (?,?)", (gid, uid))
        return self.my_group(uid)

    def join_group(self, uid: int, code: str) -> dict:
        g = self.db.one("SELECT * FROM groups_ WHERE code=?", ((code or "").strip().upper(),))
        if not g:
            raise HiveError("Group code not found", 404)
        with self.db.tx() as c:
            c.execute("DELETE FROM group_members WHERE user_id=?", (uid,))
            c.execute("INSERT INTO group_members(group_id, user_id) VALUES (?,?)", (g["id"], uid))
        return self.my_group(uid)

    def leave_group(self, uid: int):
        with self.db.tx() as c:
            c.execute("DELETE FROM group_members WHERE user_id=?", (uid,))

    def set_board_visibility(self, uid: int, show: bool):
        with self.db.tx() as c:
            c.execute("UPDATE group_members SET show_in_board=? WHERE user_id=?", (1 if show else 0, uid))

    def my_group(self, uid: int):
        m = self.db.one("SELECT g.*, m.show_in_board FROM group_members m JOIN groups_ g ON g.id=m.group_id WHERE m.user_id=?", (uid,))
        if not m:
            return None
        week = now() - 7 * 86400
        board = self.db.q("""SELECT u.username, COALESCE((SELECT SUM(w.honey) FROM work_log w WHERE w.user_id=u.id AND w.at>?),0) mi,
                             COALESCE((SELECT COUNT(*) FROM work_log w WHERE w.user_id=u.id AND w.at>?),0) cells
                             FROM group_members gm JOIN users u ON u.id=gm.user_id
                             WHERE gm.group_id=? AND gm.show_in_board=1 ORDER BY mi DESC""", (week, week, m["id"]))
        return {"name": m["name"], "code": m["code"], "show_in_board": bool(m["show_in_board"]),
                "board": [{"username": b["username"], "honey_week": b["mi"] / CENT, "cells_week": b["cells"]} for b in board]}

    def public_stats(self) -> dict:
        c = self.db.connect()
        day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        monday = day - datetime.now().weekday() * 86400
        return {
            "active_bees": c.execute("SELECT COUNT(*) FROM nodes WHERE last_seen>? AND banned=0 AND revoked=0",
                                     (now() - ACTIVE_WINDOW_S,)).fetchone()[0],
            "cells_today": c.execute("SELECT COUNT(*) FROM work_log WHERE at>=?", (day,)).fetchone()[0],
            "cells_week": c.execute("SELECT COUNT(*) FROM work_log WHERE at>=?", (monday,)).fetchone()[0],
            "weekly_goal": self.s.weekly_goal,
            "memory_items": c.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "queued_cells": c.execute("SELECT COUNT(*) FROM tasks WHERE status='queued'").fetchone()[0],
        }
