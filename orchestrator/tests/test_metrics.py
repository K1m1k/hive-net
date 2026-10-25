# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Tests for performance metrics, time remaining and reports.

Run from the orchestrator directory:  ../.venv/bin/python -m unittest tests.test_metrics -v

With a real server and real bees, checks that:
  1. every delivered cell leaves a consistent metric (non-negative timings, bytes, bee, kind)
  2. time remaining is shown while the job runs, progress never goes backwards and ends at 100%
  3. the report (.md) and the raw data (CSV) contain what is needed for an evaluation
  4. only the user who submitted a job can see its reports and metrics
  5. bees stay anonymous in reports
  6. forged or absurd metrics sent by a bee are discarded without blocking the work
"""
import base64
import csv
import io
import json
import time
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tests.test_integration import Hive, make_text, run_module, seal_for, sha256_hex


class T7_Metrics(unittest.TestCase):
    def test_metrics_time_remaining_reports(self):
        h = Hive(verify_p_new=0.3)
        try:
            req = h.user("requester")
            other = h.user("curious")
            regs = [h.node(h.user(f"bee{i}"), f"secret bee {i}") for i in range(3)]
            job = h.post("/api/jobs", {"name": "metrics", "ops": ["clean", "stats", "embed"], "text": make_text(8000, 11)}, req)
            jid = job["id"]
            for r in regs:
                h.start_node(r)
            # 2. time remaining and progress while the job runs
            seen_eta, last_p = False, 0.0
            t0 = time.time()
            while True:
                j = h.get(f"/api/jobs/{jid}", req)
                live = j["live"]
                self.assertIn("eta_s", live)
                self.assertIn("elapsed_s", live)
                if j["status"] in ("done", "failed"):
                    break
                if live["eta_s"] is not None:
                    seen_eta = True
                    self.assertGreaterEqual(live["eta_s"], 0)
                    self.assertGreater(live["speed"]["cells_min"], 0)
                if live["progress"] is not None:
                    self.assertGreaterEqual(live["progress"], 0)
                    self.assertLessEqual(live["progress"], 1)
                    # when the second phase starts the estimate is corrected: small tolerance
                    self.assertGreaterEqual(live["progress"], last_p - 0.1, "progress must not go backwards")
                    last_p = max(last_p, live["progress"])
                self.assertLess(time.time() - t0, 180, "job too slow")
                time.sleep(0.3)
            self.assertEqual(j["status"], "done")
            self.assertTrue(seen_eta, "a time remaining must be shown while the job runs")
            self.assertEqual(j["live"]["progress"], 1.0)
            self.assertEqual(j["live"]["eta_s"], 0)

            # 1. one consistent metric for every delivered cell
            c = h.db
            done = c.execute("""SELECT COUNT(*) FROM tasks t JOIN chunks ch ON ch.id=t.chunk_id
                                WHERE ch.job_id=? AND t.status='done'""", (jid,)).fetchone()[0]
            rows = c.execute("SELECT * FROM task_metrics WHERE job_id=? AND status='accepted'", (jid,)).fetchall()
            self.assertEqual(len(rows), done, "every accepted cell must have its metric")
            for r in rows:
                n = json.loads(r["node"])
                for k in ("download_ms", "key_ms", "sandbox_ms", "decrypt_ms", "compile_ms", "wasm_ms", "seal_ms", "cpu_ms"):
                    self.assertIn(k, n)
                    self.assertGreaterEqual(n[k], 0, k)
                self.assertGreater(r["out_bytes"], 0)
                self.assertGreater(n["down_bytes"], 0)
                self.assertGreaterEqual(r["leased_at"], r["created"])
                self.assertGreaterEqual(r["finished"], r["leased_at"])
                self.assertIn(r["kind"], ("primary", "verify", "dispute"))

            # 3. metric summary, report and CSV
            m = h.get(f"/api/jobs/{jid}/metrics", req)
            self.assertAlmostEqual(sum(p["share"] for p in m["phases"]), 100, delta=1)
            self.assertEqual({w["key"] for w in m["waits"]}, {"server_queue_ms", "wait_ms"})
            self.assertTrue(m["bottleneck"]["advice"])
            self.assertEqual(m["resources"]["bees"], 3)
            self.assertGreater(m["distribution"]["single_pc_compute_s"], 0)
            self.assertGreater(m["network"]["kb_in"], 0)
            self.assertIn("endpoints", m["server"])
            md = h.get(f"/api/jobs/{jid}/report", req, raw=True).decode()
            for t in ("Performance report", "Summary", "Bottleneck", "Does distribution pay off",
                      "Where each cell's time goes", "Queue waits", "Network and resources", "By operation",
                      "Bees (anonymised)", "Server"):
                self.assertIn(t, md)
            rows_csv = list(csv.DictReader(io.StringIO(h.get(f"/api/jobs/{jid}/metrics.csv", req, raw=True).decode())))
            self.assertEqual(len(rows_csv), c.execute("SELECT COUNT(*) FROM task_metrics WHERE job_id=?", (jid,)).fetchone()[0])
            self.assertIn("wasm_ms", rows_csv[0])

            # 5. anonymous bees: no bee name, user name or node id
            self.assertNotIn("secret bee", md)
            self.assertNotIn("secret bee", json.dumps(m))
            self.assertNotIn("node_id", json.dumps(m["bees"]))
            self.assertTrue(all(row["bee"].startswith("Bee ") for row in rows_csv))
            self.assertNotIn("node_id", rows_csv[0])

            # 4. another user sees nothing
            for path in (f"/api/jobs/{jid}", f"/api/jobs/{jid}/metrics", f"/api/jobs/{jid}/report", f"/api/jobs/{jid}/metrics.csv"):
                self.assertEqual(h.get(path, other).get("_status"), 404, path)
                self.assertEqual(h.get(path).get("_status"), 401, path + " without sign-in")
        finally:
            h.close()

    def test_forged_metrics_discarded(self):
        h = Hive(verify_p_new=0.0, verify_p_normal=0.0, verify_p_reliable=0.0)
        try:
            req = h.user("requester")
            reg = h.node(h.user("manual"), "manual")
            nt = reg["node_token"]
            seal_pub = base64.b64decode(reg["seal_public_key"])
            job = h.post("/api/jobs", {"name": "p", "ops": ["clean"], "text": make_text(500, 4)}, req)
            bad_metrics = [
                {"wasm_ms": -5, "download_ms": float("1e30"), "key_ms": "slow", "rss_mb": None, "extra": "x" * 10000},
                "not an object",
                {"wasm_ms": 12.5, "records": 3, "down_bytes": 100},
            ]
            statuses = []
            for i, metr in enumerate(bad_metrics):
                pk = h.post("/api/node/work", {"max_packets": 1}, nt)["packets"]
                if not pk:
                    break
                p = pk[0]
                key = base64.b64decode(h.post("/api/node/key", {"task_id": p["task_id"], "lease_token": p["lease_token"]}, nt)["key"])
                aad = base64.b64decode(p["aad"])
                out = run_module("text_clean", AESGCM(key).decrypt(base64.b64decode(p["nonce"]), base64.b64decode(p["ct"]), aad))
                body = {"task_id": p["task_id"], "lease_token": p["lease_token"], "idem_key": f"m-{i}",
                        "sealed": seal_for(seal_pub, out, aad), "result_sha256": sha256_hex(out), "metrics": metr}
                statuses.append(h.post("/api/node/result", body, nt)["status"])
            self.assertTrue(statuses)
            self.assertTrue(all(s == "accepted" for s in statuses), f"metrics must never block results: {statuses}")
            for r in h.db.execute("SELECT node FROM task_metrics WHERE job_id=?", (job["id"],)).fetchall():
                n = json.loads(r["node"])
                self.assertNotIn("extra", n, "unknown fields discarded")
                self.assertNotIn("key_ms", n, "non-numeric values discarded")
                for k, v in n.items():
                    self.assertGreaterEqual(v, 0, k)
                    self.assertLess(v, 7 * 86400 * 1000, k)
            self.assertEqual(h.db.execute("SELECT COUNT(*) FROM task_metrics WHERE job_id=?", (job["id"],)).fetchone()[0], len(statuses))
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
