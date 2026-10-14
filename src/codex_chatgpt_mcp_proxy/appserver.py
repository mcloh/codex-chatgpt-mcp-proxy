"""Codex 0.162.1 newline-delimited JSON-RPC over dedicated stdio."""

import asyncio
import json
import os


class AppServer:
    def __init__(self, config, approval):
        self.config, self.approval = config, approval
        self.pending = {}
        self.notifications = asyncio.Queue(maxsize=4000)
        self.next_id = 0
        self.requests = set()
        self.process = None

    async def start(self, cwd):
        # Bridge tokens are never inherited by the model's tools or Codex subprocess.
        environment = {k: v for k, v in os.environ.items() if not k.startswith("BRIDGE_")}
        self.process = await asyncio.create_subprocess_exec(
            self.config.codex,
            "app-server",
            "--stdio",
            "-c",
            f'sandbox_mode="{self.config.sandbox}"',
            "-c",
            'approval_policy="on-request"',
            "-c",
            'approvals_reviewer="user"',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=environment,
            limit=4 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self.read())
        self.stderr_reader = asyncio.create_task(self.drain_stderr())
        await self.call(
            "initialize",
            {
                "clientInfo": {"name": "codex_chatgpt_mcp_proxy", "version": "0.1.0"},
                "capabilities": {"experimentalApi": False},
            },
        )
        await self.send({"method": "initialized"})

    async def drain_stderr(self):
        # Drain without forwarding logs that may include local paths or account details.
        while await self.process.stderr.readline():
            pass

    async def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method, params, timeout=30):
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                msg = json.loads(line)
                if "method" in msg:
                    if "id" in msg:
                        job = asyncio.create_task(self.handle_request(msg))
                        self.requests.add(job)
                        job.add_done_callback(self.requests.discard)
                    else:
                        await self.notifications.put(msg)
                elif msg.get("id") in self.pending:
                    future = self.pending[msg["id"]]
                    if not future.done():
                        if "error" in msg:
                            future.set_exception(
                                RuntimeError(
                                    "Codex RPC "
                                    + str(msg["error"].get("code"))
                                    + ": "
                                    + str(msg["error"].get("message", ""))[:1000]
                                )
                            )
                        else:
                            future.set_result(msg.get("result", {}))
            raise RuntimeError("Codex App Server disconnected")
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)
            await self.notifications.put({"method": "bridge/disconnected", "params": {}})

    async def handle_request(self, msg):
        if msg["method"] in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        ):
            try:
                decision = await self.approval(msg)
                await self.send({"id": msg["id"], "result": {"decision": decision}})
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.send({"id": msg["id"], "result": {"decision": "decline"}})
        else:
            await self.send(
                {
                    "id": msg["id"],
                    "error": {
                        "code": -32601,
                        "message": "Unsupported local request; denied by bridge",
                    },
                }
            )
            await self.notifications.put(
                {"method": "bridge/requestDenied", "params": {"method": msg["method"]}}
            )

    async def thread(self, cwd, thread_id=None):
        params = {
            "cwd": str(cwd),
            "sandbox": self.config.sandbox,
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
        }
        if self.config.model:
            params["model"] = self.config.model
        if thread_id:
            params["threadId"] = thread_id
        response = await self.call("thread/resume" if thread_id else "thread/start", params)
        return response["thread"]["id"]

    async def turn(self, thread_id, instruction):
        result = await self.call(
            "turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": instruction}]}
        )
        return result["turn"]["id"]

    async def interrupt(self, thread_id, turn_id):
        return await self.call(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10
        )

    async def close(self):
        for job in list(self.requests):
            job.cancel()
        if self.requests:
            await asyncio.gather(*self.requests, return_exceptions=True)
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        if self.process:
            await asyncio.gather(self.reader, self.stderr_reader, return_exceptions=True)
