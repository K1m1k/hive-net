# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Performance metrics: per-phase timings, bytes exchanged, resources, time remaining.

They show where time is lost (bottlenecks) and produce a report backed by real
numbers. Nodes send their timings TOGETHER with the result: no extra messages.
"""
import csv
import io
import json
import os
import platform
import resource
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from .modules import OPS, units_for

# timings a node may send (milliseconds), with bounds that discard absurd values
NODE_FIELDS = ["download_ms", "wait_ms", "key_ms", "sandbox_ms", "decrypt_ms", "compile_ms", "wasm_ms",
               "seal_ms", "prev_upload_ms", "cpu_ms", "rss_mb", "wasm_mem_mb", "down_bytes", "records", "cores"]
MAX_VALUE = 7 * 86400 * 1000

# phases of a cell, in the order they happen
PHASES = [
    ("server_queue_ms", "Waiting for a free bee (server)"),
    ("download_ms", "Packet download"),
    ("wait_ms", "Queued on the node"),
    ("key_ms", "Key request"),
    ("sandbox_ms", "Sandbox start-up"),
    ("decrypt_ms", "Decryption"),
    ("compile_ms", "Module preparation"),
    ("wasm_ms", "Computation (WebAssembly module)"),
    ("seal_ms", "Result sealing"),
    ("upload_ms", "Result delivery (estimate)"),
    ("server_ms", "Result check (server)"),
]
LABEL = dict(PHASES)
# waits: the cell sits in a queue (they show whether bees are missing, not what a cell costs)
WAITS = ["server_queue_ms", "wait_ms"]
# active phases: where the time goes while a cell is actually being worked on
ACTIVE = [k for k, _ in PHASES if k not in WAITS]
LOW_USEFUL = 20.0
# phases performed by the node for a cell: they measure the "work" of the network
NODE_WORK = ["download_ms", "key_ms", "sandbox_ms", "decrypt_ms", "compile_ms", "wasm_ms", "seal_ms"]

ADVICE = {
    "server_queue_ms": "Cells are waiting for a bee to become free: more active bees are needed, or bees should ask for work more often.",
    "download_ms": "Packet downloads are slow: slow network or large packets. Consider compression or a binary format (MessagePack).",
    "wait_ms": "Packets sit on the node before being processed: the node takes too many at once for its speed (reduce packets per request).",
    "key_ms": "The separate key request costs an extra network round trip: it matters less with larger packets.",
    "sandbox_ms": "Starting the sandbox costs too much compared with the work: make sure the sandbox process is reused between cells.",
    "decrypt_ms": "Decryption is heavy compared with the computation: packets are too small for such a light operation.",
    "compile_ms": "Preparing the WebAssembly module for every cell costs too much: keep it compiled in memory.",
    "wasm_ms": "Almost all the time goes into actual computation: this is the healthy case. More bees are needed to go faster.",
    "seal_ms": "Sealing the result is heavy compared with the computation: packets are too small for such a light operation.",
    "upload_ms": "Result delivery is slow: slow upstream network or large results (e.g. embeddings). Consider compression.",
    "server_ms": "The server takes too long to accept results: the database or the server CPU is the limit.",
}


def clean_node_metrics(m) -> dict:
    out = {}
    if not isinstance(m, dict):
        return out
    for k in NODE_FIELDS:
        v = m.get(k)
        if isinstance(v, (int, float)) and v == v and 0 <= v <= MAX_VALUE:
            out[k] = float(v)
    return out


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


def _r(x, d=1):
    return round(float(x or 0), d)


# ---------------------------------------------------------------------- server requests
class RequestStats:
    """How much time the server spends on each kind of request (since start-up)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_ep = defaultdict(lambda: {"n": 0, "ms": 0.0, "max": 0.0, "errors": 0, "bytes_out": 0,
                                          "recent": deque(maxlen=200)})
        self.started = time.time()

    def add(self, name: str, ms: float, status: int, nbytes: int):
        with self.lock:
            e = self.by_ep[name]
            e["n"] += 1; e["ms"] += ms; e["max"] = max(e["max"], ms); e["bytes_out"] += nbytes
            e["recent"].append(ms)
            if status >= 400:
                e["errors"] += 1

    def snapshot(self) -> list[dict]:
        with self.lock:
            rows = [{"endpoint": k, "requests": e["n"], "avg_ms": _r(e["ms"] / e["n"], 2) if e["n"] else 0,
                     "p95_ms": _r(_pct(list(e["recent"]), 95), 2), "max_ms": _r(e["max"], 2),
                     "errors": e["errors"], "kb_out": _r(e["bytes_out"] / 1024), "total_s": _r(e["ms"] / 1000, 2)}
                    for k, e in self.by_ep.items()]
        return sorted(rows, key=lambda r: -r["total_s"])


def server_resources(started: float) -> dict:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    up = max(1e-6, time.time() - started)
    rss = None
    try:
        with open("/proc/self/statm") as f:
            rss = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    except (OSError, ValueError):
        pass
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    maxrss = ru.ru_maxrss / (1048576 if platform.system() == "Darwin" else 1024)
    return {"uptime_s": _r(up, 0), "cpu_s": _r(ru.ru_utime + ru.ru_stime, 2),
            "cpu_cores_avg": _r((ru.ru_utime + ru.ru_stime) / up, 3), "rss_mb": _r(rss if rss else maxrss),
            "rss_max_mb": _r(maxrss), "load_1m": _r(load[0], 2), "cores": os.cpu_count(),
            "system": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}"}


# ---------------------------------------------------------------------- recording
def record(c, task, node, status: str, server_ms: float, sealed: dict | None, node_metrics) -> None:
    """Stores the metrics of one cell. Must be called inside the transaction that accepts the result.
    Metrics come from the node: only known, numeric fields within sensible bounds are kept."""
    m = clean_node_metrics(node_metrics)
    ch = c.execute("SELECT job_id, op, units, n_records FROM chunks WHERE id=?", (task["chunk_id"],)).fetchone()
    if not ch:
        return
    out_bytes = sum(len(str(sealed.get(k, ""))) for k in ("epk", "nonce", "ct")) if isinstance(sealed, dict) else 0
    c.execute("""INSERT OR REPLACE INTO task_metrics(task_id, job_id, chunk_id, node_id, kind, op, status, records,
                 units, created, leased_at, finished, server_ms, in_bytes, out_bytes, node)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (task["id"], ch["job_id"], task["chunk_id"], node["id"], task["kind"], ch["op"], status,
               int(m.get("records", ch["n_records"])), ch["units"], task["created"], task["leased_at"],
               time.time(), server_ms, int(m.get("down_bytes", 0)), out_bytes, json.dumps(m)))


def _rows(db, job_id=None, since=None):
    sql, args = "SELECT * FROM task_metrics WHERE 1=1", []
    if job_id is not None:
        sql += " AND job_id=?"; args.append(job_id)
    if since is not None:
        sql += " AND finished>=?"; args.append(since)
    out = []
    for r in db.q(sql + " ORDER BY finished", args):
        d = dict(r)
        d.update(json.loads(d.pop("node") or "{}"))
        d["records"] = int(d.get("records") or 0)
        d["server_queue_ms"] = max(0.0, (d["leased_at"] - d["created"]) * 1000) if d["leased_at"] else 0.0
        d["upload_ms"] = d.get("prev_upload_ms", 0.0)
        d["node_work_ms"] = sum(d.get(k, 0.0) for k in NODE_WORK)
        out.append(d)
    return out


# ---------------------------------------------------------------------- time remaining (live)
WINDOW_S = 90


def _ms_per_unit(db) -> dict:
    """How much node work (ms) one unit of each operation costs, based on the network's latest cells."""
    res = {}
    for op in OPS:
        rows = db.q("""SELECT units, node FROM task_metrics WHERE op=? AND status='accepted'
                       ORDER BY finished DESC LIMIT 200""", (op,))
        tot_u = sum(r["units"] for r in rows)
        tot_ms = sum(sum(json.loads(r["node"] or "{}").get(k, 0.0) for k in NODE_WORK) for r in rows)
        res[op] = tot_ms / tot_u if tot_u > 0 and tot_ms > 0 else None
    known = [v for v in res.values() if v]
    fallback = sum(known) / len(known) if known else 1000.0
    return {k: (v or fallback) for k, v in res.items()}


def job_live(db, job) -> dict:
    """Progress, speed and time remaining for a job, like a download dialog."""
    jid = job["id"]
    t = time.time()
    status = job["status"]
    if status in ("done", "failed"):
        wall = (job["finished"] or t) - job["created"]
        return {"progress": 1.0 if status == "done" else None, "eta_s": 0 if status == "done" else None,
                "elapsed_s": _r(wall, 0), "speed": None, "eta_note": ""}
    mpu = _ms_per_unit(db)
    remaining = defaultdict(float)
    done_work = 0.0
    for ch in db.q("SELECT op, phase, units, status FROM chunks WHERE job_id=?", (jid,)):
        if ch["status"] == "done":
            done_work += ch["units"] * mpu[ch["op"]]
        else:
            remaining[ch["op"]] += ch["units"] * (0.5 if ch["status"] == "verifying" else 1.0)
    note = ""
    if status == "phase1":
        # the second phase does not exist yet: estimate it from the size of the text
        clean_units = sum(r["units"] for r in db.q("SELECT units FROM chunks WHERE job_id=? AND phase=1", (jid,)))
        nbytes = clean_units * OPS["clean"].bytes_per_unit
        for op in job["ops"].split(","):
            if op in ("stats", "embed"):
                remaining[op] += units_for(OPS[op], int(nbytes))
        note = "estimate: the second phase will be measured once it starts"
    rem_work = sum(u * mpu[op] for op, u in remaining.items())
    # speed: work completed for this job over the last few seconds
    first = db.one("SELECT MIN(leased_at) FROM tasks WHERE chunk_id IN (SELECT id FROM chunks WHERE job_id=?)", (jid,))[0]
    start = max(t - WINDOW_S, first or job["created"])
    win = max(1.0, t - start)
    recent = db.q("SELECT units, op, records, in_bytes, out_bytes FROM task_metrics WHERE job_id=? AND finished>=? AND status='accepted'",
                  (jid, start))
    rate = sum(r["units"] * mpu[r["op"]] for r in recent) / win  # ms of work per second
    if not recent:
        # no cell of this job finished recently: use the speed of the whole network
        active = max(1, db.one("SELECT COUNT(*) FROM jobs WHERE status IN ('phase1','phase2')")[0])
        net = db.q("SELECT units, op FROM task_metrics WHERE finished>=? AND status='accepted'", (t - WINDOW_S,))
        rate = sum(r["units"] * mpu[r["op"]] for r in net) / WINDOW_S / active
        if not note:
            note = "estimated from network speed"
    eta = rem_work / rate if rate > 0 else None
    total = done_work + rem_work
    speed = {
        "cells_min": _r(len(recent) / win * 60, 1),
        "records_s": _r(sum(r["records"] for r in recent) / win, 1),
        "kb_s": _r(sum((r["in_bytes"] or 0) + (r["out_bytes"] or 0) for r in recent) / 1024 / win, 1),
        "bees": db.one("""SELECT COUNT(DISTINCT node_id) FROM tasks WHERE status='leased'
                          AND chunk_id IN (SELECT id FROM chunks WHERE job_id=?)""", (jid,))[0],
    }
    if eta is not None and recent and len(recent) < 3:
        eta, note = None, "measuring speed..."
    if eta is None and not note.startswith("measuring"):
        note = "waiting for free bees"
    return {"progress": _r(done_work / total, 4) if total > 0 else 0.0, "eta_s": _r(eta, 0) if eta is not None else None,
            "elapsed_s": _r(t - job["created"], 0), "speed": speed, "eta_note": note}


# ---------------------------------------------------------------------- job analysis
def job_metrics(db, job, req_stats: RequestStats | None = None) -> dict:
    rows = _rows(db, job_id=job["id"])
    acc = [r for r in rows if r["status"] == "accepted"]
    prim = [r for r in acc if r["kind"] == "primary"]
    ver = [r for r in acc if r["kind"] != "primary"]
    end = job["finished"] or time.time()
    wall = max(1e-6, end - job["created"])
    phases, waits = [], []
    tot_active = sum(sum(r.get(k, 0.0) for k in ACTIVE) for r in acc) or 1.0
    for k, label in PHASES:
        v = [r.get(k, 0.0) for r in acc]
        s = sum(v)
        row = {"key": k, "label": label, "avg_ms": _r(s / len(v), 2) if v else 0, "p95_ms": _r(_pct(v, 95), 2),
               "total_s": _r(s / 1000, 3)}
        if k in WAITS:
            waits.append(row)
        else:
            row["share"] = _r(100 * s / tot_active)
            phases.append(row)
    compute = [p for p in phases if p["key"] == "wasm_ms"][0]
    # bottleneck: the overhead phase (not the computation) that weighs the most
    overhead = [p for p in phases if p["key"] != "wasm_ms"]
    worst = max(overhead, key=lambda p: p["total_s"]) if acc else None
    if worst and compute["total_s"] >= worst["total_s"]:
        worst = compute
    advice = ADVICE[worst["key"]] if worst else ""
    if acc and compute["share"] < LOW_USEFUL:
        advice += (f" Overall, actual computation is only {compute['share']}% of the time: packets are too small or the"
                   " operation too light for distribution to pay off.")
    single_pc_s = sum(r.get("wasm_ms", 0.0) for r in prim) / 1000
    nodes = defaultdict(lambda: {"cells": 0, "wasm_s": 0.0, "work_s": 0.0, "records": 0, "rejected": 0})
    for r in rows:
        n = nodes[r["node_id"]]
        if r["status"] != "accepted":
            n["rejected"] += 1
            continue
        n["cells"] += 1; n["wasm_s"] += r.get("wasm_ms", 0.0) / 1000
        n["work_s"] += (r["node_work_ms"] + r.get("upload_ms", 0.0)) / 1000
        n["records"] += r["records"]
    node_rows = []
    for i, (nid, n) in enumerate(sorted(nodes.items(), key=lambda kv: -kv[1]["cells"]), 1):
        node_rows.append({"bee": f"Bee {i}", "cells": n["cells"], "rejected": n["rejected"], "records": n["records"],
                          "compute_s": _r(n["wasm_s"], 3), "busy_share": _r(min(100.0, 100 * n["work_s"] / wall)),
                          "records_s": _r(n["records"] / n["work_s"], 1) if n["work_s"] else 0})
    n_nodes = len([n for n in nodes.values() if n["cells"]])
    useful_all = sum(r.get("wasm_ms", 0.0) for r in acc) / 1000
    ver_s = sum(r.get("wasm_ms", 0.0) for r in ver) / 1000
    by_op = defaultdict(lambda: {"cells": 0, "records": 0, "wasm_s": 0.0, "kb": 0.0})
    for r in prim:
        o = by_op[r["op"]]
        o["cells"] += 1; o["records"] += r["records"]; o["wasm_s"] += r.get("wasm_ms", 0.0) / 1000
        o["kb"] += ((r["in_bytes"] or 0) + (r["out_bytes"] or 0)) / 1024
    ops = [{"op": k, "cells": v["cells"], "records": v["records"], "compute_s": _r(v["wasm_s"], 3),
            "avg_cell_ms": _r(1000 * v["wasm_s"] / v["cells"], 2) if v["cells"] else 0, "kb": _r(v["kb"])}
           for k, v in by_op.items()]
    bytes_in = sum(r["in_bytes"] or 0 for r in acc)
    bytes_out = sum(r["out_bytes"] or 0 for r in acc)
    res = {
        "job": {"id": job["id"], "name": job["name"], "status": job["status"], "ops": job["ops"],
                "records": job["n_records"], "unique": job["n_unique"],
                "created": datetime.fromtimestamp(job["created"]).isoformat(timespec="seconds"),
                "wall_s": _r(wall, 2)},
        "cells": {"primary": len(prim), "verify": len(ver), "rejected": len(rows) - len(acc),
                  "from_memory_units": _r(job["units_cached"], 3), "distributed_units": _r(job["units_distributed"], 3)},
        "phases": phases,
        "useful_share": compute["share"] if acc else 0,
        "bottleneck": {"key": worst["key"], "label": worst["label"], "share": worst["share"],
                       "advice": advice} if worst else None,
        "waits": waits,
        "network": {"kb_in": _r(bytes_in / 1024), "kb_out": _r(bytes_out / 1024),
                    "kb_per_record": _r((bytes_in + bytes_out) / 1024 / max(1, sum(r["records"] for r in prim)), 3),
                    "kb_s": _r((bytes_in + bytes_out) / 1024 / wall, 1)},
        "resources": {"bees": n_nodes,
                      "cpu_s": _r(sum(r.get("cpu_ms", 0.0) for r in acc) / 1000, 3),
                      "rss_max_mb": _r(max([r.get("rss_mb", 0.0) for r in acc] or [0])),
                      "wasm_mem_max_mb": _r(max([r.get("wasm_mem_mb", 0.0) for r in acc] or [0]))},
        "verification": {"compute_s": _r(ver_s, 3), "share_of_compute": _r(100 * ver_s / useful_all) if useful_all else 0},
        "distribution": {
            "single_pc_compute_s": _r(single_pc_s, 3),
            "wall_s": _r(wall, 2),
            "speedup_estimate": _r(single_pc_s / wall, 3),
            "utilization": _r(100 * useful_all / (wall * n_nodes)) if n_nodes else 0,
        },
        "ops": ops,
        "bees": node_rows,
    }
    if req_stats:
        res["server"] = {"resources": server_resources(req_stats.started), "endpoints": req_stats.snapshot()}
    return res


def job_csv(db, job_id: int) -> str:
    rows = _rows(db, job_id=job_id)
    cols = ["task_id", "kind", "op", "status", "records", "units", "in_bytes", "out_bytes"] + \
           [k for k, _ in PHASES] + ["cpu_ms", "rss_mb", "wasm_mem_mb", "finished"]
    node_ids = {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["bee"] + cols)
    for r in rows:
        bee = node_ids.setdefault(r["node_id"], f"Bee {len(node_ids) + 1}")
        w.writerow([bee] + [round(r.get(c) or 0, 3) if isinstance(r.get(c), float) else r.get(c, "") for c in cols])
    return buf.getvalue()


def _hms(s):
    s = int(round(s or 0))
    return f"{s // 3600}h {s % 3600 // 60}m {s % 60}s" if s >= 3600 else (f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s")


def job_report_md(m: dict) -> str:
    j, d = m["job"], m["distribution"]
    L = [f"# Performance report — job {j['id']} “{j['name']}”", "",
         f"Generated: {datetime.now().isoformat(timespec='seconds')}  ",
         f"Status: {j['status']} · operations: {j['ops']} · texts: {j['records']} (unique: {j['unique'] or '?'})  ",
         f"Submitted: {j['created']} · duration: **{_hms(j['wall_s'])}** ({j['wall_s']} s)", ""]
    L += ["## Summary", ""]
    if m["bottleneck"]:
        b = m["bottleneck"]
        L += [f"- Bottleneck: **{b['label']}** ({b['share']}% of the cells' active time)",
              f"- What to do: {b['advice']}"]
    L += [f"- Useful work (computation) as a share of the cells' active time: **{m['useful_share']}%**",
          f"- Bees that worked: {m['resources']['bees']} · cells: {m['cells']['primary']} + {m['cells']['verify']} for verification"
          f" · rejected: {m['cells']['rejected']}",
          f"- Reused from memory: {m['cells']['from_memory_units']} units not recomputed (distributed: {m['cells']['distributed_units']})", ""]
    L += ["## Does distribution pay off? (estimate for this job)", "",
          "| Metric | Value |", "|---|---:|",
          f"| Pure computation a single PC would have done | {d['single_pc_compute_s']} s |",
          f"| Actual duration on the network | {d['wall_s']} s |",
          f"| Estimated speed-up (>1 = the network is faster) | {d['speedup_estimate']}× |",
          f"| Effective bee utilisation (computation / available time) | {d['utilization']}% |",
          f"| Verification cost relative to computation | {m['verification']['share_of_compute']}% |", "",
          "> Estimate: the \"single PC\" figure is the sum of the computation times measured on the bees, without network "
          "or encryption. The definitive proof is the benchmark of 1 PC against 2/5/10 nodes on the same job.", ""]
    L += ["## Where each cell's time goes (active time)", "", "| Phase | Mean (ms) | 95th percentile (ms) | Total (s) | Share |",
          "|---|---:|---:|---:|---:|"]
    for p in m["phases"]:
        L.append(f"| {p['label']} | {p['avg_ms']} | {p['p95_ms']} | {p['total_s']} | {p['share']}% |")
    L += ["", "## Queue waits", "", "| Wait | Mean (ms) | 95th percentile (ms) | Total (s) |", "|---|---:|---:|---:|"]
    for p in m["waits"]:
        L.append(f"| {p['label']} | {p['avg_ms']} | {p['p95_ms']} | {p['total_s']} |")
    L += ["", "Waits are normal when a job has more cells than there are bees available: they show how much more bees"
          " would help, not what a cell costs."]
    n = m["network"]; r = m["resources"]
    L += ["", "## Network and resources", "",
          f"- Data sent to bees: {n['kb_in']} KB · received from bees: {n['kb_out']} KB · {n['kb_per_record']} KB per text · average {n['kb_s']} KB/s",
          f"- CPU used by bees: {r['cpu_s']} s · peak memory of a bee: {r['rss_max_mb']} MB · module memory: {r['wasm_mem_max_mb']} MB", ""]
    if m["ops"]:
        L += ["## By operation", "", "| Operation | Cells | Texts | Computation (s) | Mean per cell (ms) | KB exchanged |",
              "|---|---:|---:|---:|---:|---:|"]
        L += [f"| {o['op']} | {o['cells']} | {o['records']} | {o['compute_s']} | {o['avg_cell_ms']} | {o['kb']} |" for o in m["ops"]]
        L.append("")
    if m["bees"]:
        L += ["## Bees (anonymised)", "", "| Bee | Cells | Rejected | Texts | Computation (s) | Busy | Texts/s |",
              "|---|---:|---:|---:|---:|---:|---:|"]
        L += [f"| {b['bee']} | {b['cells']} | {b['rejected']} | {b['records']} | {b['compute_s']} | {b['busy_share']}% | {b['records_s']} |"
              for b in m["bees"]]
        L.append("")
    if "server" in m:
        s = m["server"]["resources"]
        L += ["## Server", "",
              f"- {s['system']}, {s['cores']} cores · up for {_hms(s['uptime_s'])}",
              f"- CPU used: {s['cpu_s']} s (average {s['cpu_cores_avg']} cores) · memory: {s['rss_mb']} MB (peak {s['rss_max_mb']} MB) · load: {s['load_1m']}",
              "", "| Request | Count | Mean (ms) | 95th pct. (ms) | Max (ms) | Errors | KB sent | Total time (s) |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        L += [f"| {e['endpoint']} | {e['requests']} | {e['avg_ms']} | {e['p95_ms']} | {e['max_ms']} | {e['errors']} | {e['kb_out']} | {e['total_s']} |"
              for e in m["server"]["endpoints"]]
        L.append("")
    L += ["## How to read these figures", "",
          "- **Waiting for a free bee**: time during which the cell is ready but no bee picks it up.",
          "- **Useful work**: the share of active time spent on actual computation; the rest is the cost of distribution (network, encryption, start-up).",
          "- **Texts/s per bee**: texts processed per second of active time (computation plus overhead), i.e. the bee's real speed.",
          "- **Result delivery**: measured by the bee on its previous delivery, so it is an estimate.",
          "- The raw data for every cell is in the CSV file you can download from the panel.", ""]
    return "\n".join(L)
