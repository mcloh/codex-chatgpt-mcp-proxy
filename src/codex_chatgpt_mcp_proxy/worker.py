import asyncio
import json
import logging
import random
import time
import uuid

import httpx

from . import __version__
from .appserver import AppServer
from .journal import Journal

log = logging.getLogger("bridge.worker")


class Worker:
    def __init__(self, config, adapter=AppServer, client=None):
        config.validate()
        self.config, self.adapter = config, adapter
        self.journal = Journal(config.state)
        self.http = client or httpx.AsyncClient(
            base_url=config.url,
            headers={"Authorization": "Bearer " + config.token},
            timeout=15,
            follow_redirects=False,
        )
        self.stop = asyncio.Event()
        self.active = None
        self.lease_seconds = 45

    async def post(self, path, body=None):
        r = await self.http.post("/api/worker" + path, json=body or {})
        r.raise_for_status()
        return r.json()

    def queue(self, task, endpoint, body):
        if endpoint == "events":
            count = task.get("event_count", 0)
            if count >= 2000:
                return
            task["event_count"] = count + 1
            self.journal.save(task)
        self.journal.queue(
            "/tasks/" + task["id"] + "/" + endpoint, {"attempt": task["attempt"], **body}
        )

    async def flush(self):
        for record in self.journal.pending():
            await self.post(record["path"], json.loads(record["body"]))
            self.journal.ack(record["seq"])

    async def recover(self):
        # No replay of turn/start. Resend journaled outcome, or declare uncertainty.
        await self.flush()
        for task in self.journal.tasks():
            r = await self.http.get(
                "/api/worker/tasks/" + task["id"], params={"attempt": task["attempt"]}
            )
            r.raise_for_status()
            remote = r.json()
            if remote["state"] in ("succeeded", "failed", "cancelled", "timed_out"):
                self.journal.remove(task["id"])
                continue
            if not task.get("final_queued"):
                self.queue(
                    task,
                    "finish",
                    {
                        "state": "needs_reconciliation",
                        "error": "Worker restarted with uncertain execution; inspect locally. Task will not be replayed.",
                    },
                )
                task["final_queued"] = True
                self.journal.save(task)
            await self.flush()
            self.journal.remove(task["id"])

    async def run(self):
        backoff = self.config.poll
        recovered = False
        try:
            while not self.stop.is_set():
                try:
                    r = await self.post(
                        "/register",
                        {
                            "worker_id": self.config.worker_id,
                            "protocol": "1",
                            "version": __version__,
                            "workspaces": list(self.config.workspaces),
                        },
                    )
                    self.lease_seconds = r["lease_seconds"]
                    if not recovered:
                        await self.recover()
                        recovered = True
                    await self.post("/heartbeat")
                    await self.flush()
                    task = (await self.post("/claim"))["task"]
                    if task:
                        self.active = task
                        self.journal.save(task)
                        await self.execute(task)
                        self.active = None
                        await self.flush()
                        self.journal.remove(task["id"])
                    backoff = self.config.poll
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
                        400,
                        401,
                        403,
                        409,
                        422,
                    ):
                        log.error(
                            json.dumps(
                                {
                                    "event": "worker_requires_intervention",
                                    "http_status": exc.response.status_code,
                                }
                            )
                        )
                        raise
                    log.warning(
                        json.dumps(
                            {"event": "connection_retry", "backoff_seconds": round(backoff, 1)}
                        )
                    )
                    backoff = min(60, max(self.config.poll, backoff * 2))
                try:
                    await asyncio.wait_for(self.stop.wait(), backoff * random.uniform(0.8, 1.2))
                except TimeoutError:
                    pass
        finally:
            await self.http.aclose()

    async def execute(self, task):
        start = time.monotonic()
        cancel = asyncio.Event()
        cancellation = None
        output = ""
        adapter = None

        async def approval(msg):
            approval_id = self.journal.approval(task["id"], msg)
            self.queue(task, "progress", {"state": "awaiting_local_approval"})
            self.queue(
                task,
                "events",
                {
                    "event_id": str(uuid.uuid4()),
                    "payload": {
                        "type": "approval_required",
                        "approval_id": approval_id,
                        "method": msg["method"],
                    },
                },
            )
            deadline = time.monotonic() + self.config.approval_timeout
            try:
                while (
                    time.monotonic() < deadline and not cancel.is_set() and not self.stop.is_set()
                ):
                    decision = self.journal.decision(approval_id)
                    if decision:
                        return decision
                    await asyncio.sleep(0.2)
                return "decline"
            finally:
                self.journal.done(approval_id)
                self.queue(task, "progress", {"state": "running"})

        async def monitor():
            nonlocal cancellation
            while not cancel.is_set():
                if self.stop.is_set():
                    cancellation = "cancelled"
                    cancel.set()
                    return
                if time.monotonic() - start >= task["timeout"]:
                    cancellation = "timed_out"
                    cancel.set()
                    return
                try:
                    await self.post("/heartbeat")
                    r = await self.post(
                        "/tasks/" + task["id"] + "/renew", {"attempt": task["attempt"]}
                    )
                    await self.flush()
                    if r["state"] == "cancel_requested":
                        cancellation = "cancelled"
                        cancel.set()
                        return
                except httpx.TransportError:
                    pass  # Bounded task keeps running, with durable outbox.
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code < 500 and exc.response.status_code != 429:
                        cancellation = "needs_reconciliation"
                        cancel.set()
                        return
                try:
                    await asyncio.wait_for(cancel.wait(), min(1.0, self.lease_seconds / 3))
                except TimeoutError:
                    pass

        monitor_task = asyncio.create_task(monitor())
        final = None
        try:
            cwd = self.config.resolve_workspace(task["workspace"])
            adapter = self.adapter(self.config, approval)
            await adapter.start(cwd)
            task["thread_id"] = await adapter.thread(cwd, task.get("thread_id"))
            self.journal.save(task)
            self.queue(task, "progress", {"state": "running", "thread_id": task["thread_id"]})
            # Store intent before issuing side-effectful turn/start.
            task["turn_start_intent"] = True
            self.journal.save(task)
            if cancel.is_set():
                final = {"state": cancellation or "cancelled"}
            else:
                task["turn_id"] = await adapter.turn(task["thread_id"], task["instruction"])
                self.journal.save(task)
                self.queue(
                    task,
                    "progress",
                    {
                        "state": "running",
                        "thread_id": task["thread_id"],
                        "turn_id": task["turn_id"],
                    },
                )
                interrupt_sent = False
                interrupt_deadline = None
                while True:
                    if cancel.is_set() and not interrupt_sent:
                        await adapter.interrupt(task["thread_id"], task["turn_id"])
                        interrupt_sent = True
                        interrupt_deadline = time.monotonic() + 15
                    if interrupt_deadline and time.monotonic() > interrupt_deadline:
                        final = {
                            "state": "needs_reconciliation",
                            "error": "Interrupt acknowledgement did not establish turn completion",
                        }
                        break
                    try:
                        msg = await asyncio.wait_for(adapter.notifications.get(), 0.1)
                    except TimeoutError:
                        continue
                    method, params = msg["method"], msg.get("params", {})
                    if params.get("threadId") and params["threadId"] != task["thread_id"]:
                        continue
                    if params.get("turnId") and params["turnId"] != task["turn_id"]:
                        continue
                    if method == "bridge/disconnected":
                        raise RuntimeError("Codex process disconnected during execution")
                    if method == "item/completed":
                        item = params.get("item", {})
                        if item.get("type") == "agentMessage":
                            output = (output + "\n" + item.get("text", ""))[-64000:]
                    if method in (
                        "turn/started",
                        "turn/completed",
                        "item/started",
                        "item/completed",
                        "bridge/requestDenied",
                    ):
                        self.queue(
                            task,
                            "events",
                            {
                                "event_id": str(uuid.uuid4()),
                                "payload": {
                                    "type": method,
                                    "thread_id": task["thread_id"],
                                    "turn_id": task.get("turn_id"),
                                },
                            },
                        )
                    if method == "turn/completed":
                        turn = params["turn"]
                        status = turn["status"]
                        final = {
                            "state": cancellation
                            or (
                                "succeeded"
                                if status == "completed"
                                else "cancelled"
                                if status == "interrupted"
                                else "failed"
                            ),
                            "result": output.strip(),
                        }
                        if status == "failed":
                            final["error"] = "Codex turn failed; inspect local Codex logs"
                        break
        except Exception as exc:
            final = {
                "state": "needs_reconciliation" if task.get("turn_start_intent") else "failed",
                "error": type(exc).__name__
                + ": local execution failure; inspect worker diagnostics",
            }
            log.error(
                json.dumps(
                    {
                        "event": "execution_error",
                        "task_id": task["id"],
                        "exception_type": type(exc).__name__,
                    }
                )
            )
        finally:
            cancel.set()
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            if adapter:
                await adapter.close()
            self.queue(
                task,
                "finish",
                final
                or {
                    "state": "needs_reconciliation",
                    "error": "Execution stopped without a final outcome",
                },
            )
            task["final_queued"] = True
            self.journal.save(task)
