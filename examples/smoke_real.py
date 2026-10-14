"""Real MCP -> separate worker -> Codex test. Requires running server and worker."""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = os.environ.get("BRIDGE_SERVER_URL", "http://127.0.0.1:8000")
    token = os.environ.get("BRIDGE_MCP_ACCESS_TOKEN") or os.environ["BRIDGE_DEV_MCP_TOKEN"]
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=30) as http:
        async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, args):
                    r = await session.call_tool(name, args)
                    if r.is_error:
                        raise RuntimeError(str(r.content))
                    return r.structured_content

                async def wait(task):
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        r = await call("get_task", {"task_id": task["id"]})
                        if r["state"] in (
                            "succeeded",
                            "failed",
                            "cancelled",
                            "timed_out",
                            "needs_reconciliation",
                        ):
                            if r["state"] != "succeeded":
                                raise RuntimeError(
                                    f"Task {r['id']} ended {r['state']}: {r['error']}"
                                )
                            return r
                        await asyncio.sleep(2)
                    raise TimeoutError("Smoke deadline exceeded; inspect task and local approvals")

                first = await wait(
                    await call(
                        "submit_task",
                        {
                            "instruction": "Não use ferramentas, não leia arquivos. Responda exatamente: Ponte operacional",
                            "workspace": "demo",
                            "idempotency_key": str(uuid.uuid4()),
                            "timeout_seconds": 120,
                        },
                    )
                )
                assert "Ponte operacional" in first["result"], first["result"]
                print("Real Codex response: OK", flush=True)
                second = await wait(
                    await call(
                        "continue_task",
                        {
                            "task_id": first["id"],
                            "instruction": "Sem usar ferramentas, recorde a frase exata que você respondeu no turno anterior.",
                            "idempotency_key": str(uuid.uuid4()),
                            "timeout_seconds": 120,
                        },
                    )
                )
                assert "Ponte operacional" in second["result"]
                assert second["thread_id"] == first["thread_id"]
                print("Thread continuation across dedicated Codex processes: OK", flush=True)
                filename = "bridge-smoke-" + uuid.uuid4().hex[:12] + ".txt"
                third = await wait(
                    await call(
                        "submit_task",
                        {
                            "instruction": f"Crie apenas o arquivo {filename} no diretório de trabalho atual com o conteúdo exato Ponte operacional seguido de uma quebra de linha. Não leia outros arquivos e não altere qualquer outro arquivo.",
                            "workspace": "demo",
                            "idempotency_key": str(uuid.uuid4()),
                            "timeout_seconds": 120,
                        },
                    )
                )
                workspace = json.loads(os.environ["BRIDGE_WORKSPACE_MAP"])["demo"]
                assert (Path(workspace) / filename).read_text() == "Ponte operacional\n"
                print("Harmless file created and checked: OK", flush=True)
                evidence = {
                    "real_codex": True,
                    "tasks": [
                        {
                            "id": r["id"],
                            "thread_id": r["thread_id"],
                            "turn_id": r["turn_id"],
                            "state": r["state"],
                        }
                        for r in (first, second, third)
                    ],
                    "file": filename,
                    "chatgpt_originated": False,
                }
                Path("private/real-smoke.json").write_text(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
