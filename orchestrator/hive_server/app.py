# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Web API of the HiveNet server (Starlette)."""
import asyncio
import contextlib
import json
import logging
import threading
import time
from collections import defaultdict, deque

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .auth import node_from_token, user_from_token
from .config import Settings
from .hive import Hive, HiveError

log = logging.getLogger("hivenet")
MAX_BODY = 32 * 1024 * 1024

# Rate limits per group (per minute, from config.py):
#   auth = sign-up and login, per IP address; node = bee protocol, per node;
#   api = web panel, per user or IP


class RateLimiter:
    """In-memory sliding window. Enough for a single server; several servers need a shared store."""

    def __init__(self, limits):
        self.limits = limits
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, group: str, key: str) -> bool:
        limit, window = self.limits[group]
        now = time.monotonic()
        with self.lock:
            q = self.hits[(group, key)]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self.hits) > 50000:  # purge idle keys
                for k in [k for k, v in self.hits.items() if not v or v[-1] <= now - 600]:
                    del self.hits[k]
            return True


def create_app(settings: Settings | None = None, hive: Hive | None = None) -> Starlette:
    hive = hive or Hive(settings)
    s = hive.s
    limiter = RateLimiter({"auth": (s.rate_auth, 60), "node": (s.rate_node, 60), "api": (s.rate_api, 60)})

    def bearer(request: Request) -> str:
        h = request.headers.get("authorization", "")
        return h[7:].strip() if h.lower().startswith("bearer ") else ""

    async def body(request: Request) -> dict:
        # the limit is enforced BEFORE and DURING the read, not afterwards
        declared = request.headers.get("content-length")
        if declared and (not declared.isdigit() or int(declared) > MAX_BODY):
            raise HiveError("Request too large", 413)
        chunks, size = [], 0
        async for part in request.stream():
            size += len(part)
            if size > MAX_BODY:
                raise HiveError("Request too large", 413)
            chunks.append(part)
        raw = b"".join(chunks)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise HiveError("Invalid JSON")
        if not isinstance(data, dict):
            raise HiveError("Invalid JSON")
        return data

    def need_user(request: Request):
        u = user_from_token(hive.db, bearer(request))
        if not u:
            raise HiveError("Sign-in required", 401)
        return u

    def need_node(request: Request):
        n = node_from_token(hive.db, bearer(request))
        if not n:
            raise HiveError("Unknown node", 401)
        return n

    def endpoint(fn, group="api"):
        async def wrapped(request: Request):
            t0 = time.perf_counter()
            res = await handle(request)
            hive.req_stats.add(fn.__name__, (time.perf_counter() - t0) * 1000, res.status_code,
                               len(getattr(res, "body", b"") or b""))
            return res

        async def handle(request: Request):
            ip = request.client.host if request.client else "?"
            key = ip if group == "auth" else (bearer(request)[:16] or ip)
            if not limiter.allow(group, key):
                return JSONResponse({"error": "Too many requests: please try again shortly"}, status_code=429,
                                    headers={"Retry-After": "60"})
            try:
                res = await fn(request)
                return res if isinstance(res, Response) else JSONResponse(res)
            except HiveError as e:
                return JSONResponse({"error": str(e)}, status_code=e.status)
            except (KeyError, TypeError, ValueError) as e:
                return JSONResponse({"error": f"Missing or invalid data: {e}"}, status_code=400)
            except Exception:
                log.exception("internal error")
                return JSONResponse({"error": "Internal error"}, status_code=500)
        return wrapped

    def auth_endpoint(fn):
        return endpoint(fn, "auth")

    def node_endpoint(fn):
        return endpoint(fn, "node")

    # ------------------------------------------------ public
    @endpoint
    async def public_stats(request):
        return await run_in_threadpool(hive.public_stats)

    @endpoint
    async def modules_manifest(request):
        return Response(hive.modules.manifest_json(), media_type="application/json")

    async def module_file(request):
        name = request.path_params["name"]
        data = hive.modules.files.get(name)
        if data is None:
            return JSONResponse({"error": "Module not found"}, status_code=404)
        return Response(data, media_type="application/wasm", headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------ users
    @auth_endpoint
    async def register(request):
        d = await body(request)
        uid, tok = await run_in_threadpool(hive.register, d.get("username", ""), d.get("password", ""))
        return {"token": tok}

    @auth_endpoint
    async def login(request):
        d = await body(request)
        uid, tok = await run_in_threadpool(hive.login, d.get("username", ""), d.get("password", ""))
        return {"token": tok}

    @endpoint
    async def logout(request):
        await run_in_threadpool(hive.logout, bearer(request))
        return {"ok": True}

    @endpoint
    async def me(request):
        u = need_user(request)
        return await run_in_threadpool(hive.me, u)

    @endpoint
    async def my_ledger(request):
        u = need_user(request)
        return {"entries": await run_in_threadpool(hive.ledger_history, u["id"])}

    # ------------------------------------------------ nodes (management)
    @endpoint
    async def nodes_register(request):
        u = need_user(request)
        d = await body(request)
        return await run_in_threadpool(hive.register_node, u["id"], d.get("name", ""), d.get("os", ""))

    @endpoint
    async def nodes_delete(request):
        u = need_user(request)
        await run_in_threadpool(hive.revoke_node, u["id"], int(request.path_params["nid"]))
        return {"ok": True}

    # ------------------------------------------------ node protocol
    @node_endpoint
    async def node_hello(request):
        n = need_node(request)
        return await run_in_threadpool(hive.node_hello, n)

    @node_endpoint
    async def node_work(request):
        n = need_node(request)
        d = await body(request)
        packets = await run_in_threadpool(hive.get_work, n, d.get("max_packets", 1), d.get("lease_seconds"))
        res = {"packets": packets}
        if not packets:
            res["retry_after"] = await run_in_threadpool(hive.retry_after)
        return res

    @node_endpoint
    async def node_key(request):
        n = need_node(request)
        d = await body(request)
        return {"key": await run_in_threadpool(hive.get_key, n, int(d["task_id"]), d["lease_token"])}

    @node_endpoint
    async def node_result(request):
        n = need_node(request)
        d = await body(request)
        return await run_in_threadpool(hive.submit_result, n, int(d["task_id"]), d["lease_token"],
                                       d["idem_key"], d["sealed"], d["result_sha256"], d.get("metrics"))

    @node_endpoint
    async def node_release(request):
        n = need_node(request)
        d = await body(request)
        await run_in_threadpool(hive.release, n, [int(x) for x in d.get("task_ids", [])])
        return {"ok": True}

    # ------------------------------------------------ jobs
    @endpoint
    async def jobs_estimate(request):
        need_user(request)
        d = await body(request)
        return hive.estimate(d.get("ops", []), d.get("text", ""))

    @endpoint
    async def jobs_submit(request):
        u = need_user(request)
        d = await body(request)
        jid = await run_in_threadpool(hive.submit_job, u["id"], d.get("name", ""), d.get("ops", []), d.get("text", ""))
        return await run_in_threadpool(hive.job_info, u["id"], jid)

    @endpoint
    async def jobs_list(request):
        u = need_user(request)
        return {"jobs": await run_in_threadpool(hive.list_jobs, u["id"])}

    @endpoint
    async def job_get(request):
        u = need_user(request)
        return await run_in_threadpool(hive.job_info, u["id"], int(request.path_params["jid"]))

    @endpoint
    async def job_result(request):
        u = need_user(request)
        jid = int(request.path_params["jid"])
        emb = request.query_params.get("embeddings", "1") != "0"
        lines = list(await run_in_threadpool(lambda: list(hive.job_results(u["id"], jid, emb))))
        return StreamingResponse(iter(lines), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": f'attachment; filename="job-{jid}.jsonl"'})

    @endpoint
    async def job_metrics(request):
        u = need_user(request)
        return await run_in_threadpool(hive.job_metrics, u["id"], int(request.path_params["jid"]))

    @endpoint
    async def job_report(request):
        u = need_user(request)
        jid = int(request.path_params["jid"])
        md = await run_in_threadpool(hive.job_report, u["id"], jid)
        return Response(md, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="job-{jid}-report.md"'})

    @endpoint
    async def job_csv(request):
        u = need_user(request)
        jid = int(request.path_params["jid"])
        data = await run_in_threadpool(hive.job_csv, u["id"], jid)
        return Response(data, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="job-{jid}-metrics.csv"'})

    @endpoint
    async def job_sample(request):
        u = need_user(request)
        n = int(request.query_params.get("n", "20"))
        return await run_in_threadpool(hive.job_sample, u["id"], int(request.path_params["jid"]), n)

    @endpoint
    async def job_dispute(request):
        u = need_user(request)
        d = await body(request)
        did = await run_in_threadpool(hive.open_dispute, u["id"], int(request.path_params["jid"]), d.get("op", ""),
                                      int(d["idx"]), d.get("original"))
        return {"dispute_id": did}

    @endpoint
    async def dispute_get(request):
        u = need_user(request)
        return await run_in_threadpool(hive.dispute_status, u["id"], int(request.path_params["did"]))

    @endpoint
    async def job_forget(request):
        u = need_user(request)
        removed = await run_in_threadpool(hive.delete_job_memory, u["id"], int(request.path_params["jid"]))
        return {"removed_items": removed}

    # ------------------------------------------------ memory and groups
    @endpoint
    async def search(request):
        u = need_user(request)
        return {"results": await run_in_threadpool(hive.search, u["id"], request.query_params.get("q", ""),
                                                   int(request.query_params.get("k", "10")))}

    @endpoint
    async def group_create(request):
        u = need_user(request)
        d = await body(request)
        return await run_in_threadpool(hive.create_group, u["id"], d.get("name", ""))

    @endpoint
    async def group_join(request):
        u = need_user(request)
        d = await body(request)
        return await run_in_threadpool(hive.join_group, u["id"], d.get("code", ""))

    @endpoint
    async def group_leave(request):
        u = need_user(request)
        await run_in_threadpool(hive.leave_group, u["id"])
        return {"ok": True}

    @endpoint
    async def group_visibility(request):
        u = need_user(request)
        d = await body(request)
        await run_in_threadpool(hive.set_board_visibility, u["id"], bool(d.get("show", True)))
        return {"ok": True}

    async def index(request):
        return FileResponse(s.web_dir / "index.html")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async def loop():
            while True:
                try:
                    await run_in_threadpool(hive.maintenance)
                except Exception:
                    log.exception("maintenance")
                await asyncio.sleep(s.maintenance_every_s)
        task = asyncio.create_task(loop())
        yield
        task.cancel()

    routes = [
        Route("/", index),
        Route("/api/public/stats", public_stats),
        Route("/api/modules", modules_manifest),
        Route("/modules/{name}.wasm", module_file),
        Route("/api/auth/register", register, methods=["POST"]),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/me/ledger", my_ledger),
        Route("/api/nodes", nodes_register, methods=["POST"]),
        Route("/api/nodes/{nid:int}", nodes_delete, methods=["DELETE"]),
        Route("/api/node/hello", node_hello, methods=["POST"]),
        Route("/api/node/work", node_work, methods=["POST"]),
        Route("/api/node/key", node_key, methods=["POST"]),
        Route("/api/node/result", node_result, methods=["POST"]),
        Route("/api/node/release", node_release, methods=["POST"]),
        Route("/api/jobs/estimate", jobs_estimate, methods=["POST"]),
        Route("/api/jobs", jobs_submit, methods=["POST"]),
        Route("/api/jobs", jobs_list, methods=["GET"]),
        Route("/api/jobs/{jid:int}", job_get),
        Route("/api/jobs/{jid:int}/result", job_result),
        Route("/api/jobs/{jid:int}/sample", job_sample),
        Route("/api/jobs/{jid:int}/metrics", job_metrics),
        Route("/api/jobs/{jid:int}/report", job_report),
        Route("/api/jobs/{jid:int}/metrics.csv", job_csv),
        Route("/api/jobs/{jid:int}/dispute", job_dispute, methods=["POST"]),
        Route("/api/jobs/{jid:int}/memory", job_forget, methods=["DELETE"]),
        Route("/api/disputes/{did:int}", dispute_get),
        Route("/api/search", search),
        Route("/api/groups", group_create, methods=["POST"]),
        Route("/api/groups/join", group_join, methods=["POST"]),
        Route("/api/groups/leave", group_leave, methods=["POST"]),
        Route("/api/groups/visibility", group_visibility, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(s.web_dir)), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(GZipMiddleware, minimum_size=1024)], lifespan=lifespan)
    app.state.hive = hive
    return app
