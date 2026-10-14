import asyncio
import httpx
import pytest
from codex_chatgpt_mcp_proxy.config import WorkerConfig
from codex_chatgpt_mcp_proxy.journal import Journal
from codex_chatgpt_mcp_proxy.server import create_app
from codex_chatgpt_mcp_proxy.worker import Worker
from conftest import claim


class FakeCodex:
    starts = 0
    threads = []
    delay = 0.05
    disconnect = False
    approval_needed = False

    def __init__(self, config, approval):
        self.approval = approval
        self.notifications = asyncio.Queue()
        self.job = None

    async def start(self, cwd):
        pass

    async def thread(self, cwd, thread_id=None):
        self.threads.append(thread_id)
        return thread_id or "fake-thread"

    async def turn(self, thread_id, instruction):
        type(self).starts += 1

        async def complete():
            await asyncio.sleep(self.delay)
            if self.approval_needed:
                decision = await self.approval(
                    {
                        "id": 1,
                        "method": "item/commandExecution/requestApproval",
                        "params": {"command": "harmless"},
                    }
                )
                assert decision in ("accept", "decline", "cancel")
            if self.disconnect:
                await self.notifications.put({"method": "bridge/disconnected", "params": {}})
            else:
                await self.notifications.put(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": "fake-turn",
                            "item": {"type": "agentMessage", "text": "done"},
                        },
                    }
                )
                await self.notifications.put(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {"id": "fake-turn", "status": "completed"},
                        },
                    }
                )

        self.job = asyncio.create_task(complete())
        return "fake-turn"

    async def interrupt(self, thread_id, turn_id):
        if self.job:
            self.job.cancel()
        await self.notifications.put(
            {
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "interrupted"}},
            }
        )

    async def close(self):
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)


@pytest.fixture
def worker_setup(config, tmp_path):
    demo = tmp_path / "demo"
    demo.mkdir()
    app = create_app(config)
    wc = WorkerConfig(
        url=config.public_url,
        token=config.worker_token,
        state=str(tmp_path / "worker.sqlite"),
        workspaces={"demo": str(demo)},
        poll=0.1,
    )
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=config.public_url,
        headers={"Authorization": "Bearer " + config.worker_token},
    )
    for n, v in [
        ("starts", 0),
        ("threads", []),
        ("delay", 0.05),
        ("disconnect", False),
        ("approval_needed", False),
    ]:
        setattr(FakeCodex, n, v)
    return app.state.store, Worker(wc, FakeCodex, http)


async def run_task(store, worker, timeout=None):
    row = store.submit("hello", "demo", "key", timeout)
    task = claim(store)
    worker.journal.save(task)
    await worker.execute(task)
    await worker.flush()
    return store.get(row["id"])


async def test_worker_success_and_continuation(worker_setup):
    store, worker = worker_setup
    task = await run_task(store, worker)
    assert task["state"] == "succeeded" and task["result"] == "done"
    second = store.continue_task(task["id"], "remember", "key2")
    await worker.execute(claim(store))
    await worker.flush()
    assert store.get(second["id"])["state"] == "succeeded"
    assert FakeCodex.threads == [None, "fake-thread"]
    await worker.http.aclose()


async def test_worker_timeout_and_cancel(worker_setup):
    store, worker = worker_setup
    FakeCodex.delay = 10
    task = await run_task(store, worker, 1)
    assert task["state"] == "timed_out"
    row = store.submit("cancel", "demo", "key2")
    task = claim(store)
    store.cancel(row["id"])
    await worker.execute(task)
    await worker.flush()
    assert store.get(row["id"])["state"] == "cancelled"
    await worker.http.aclose()


async def test_codex_disconnect_requires_reconciliation(worker_setup):
    store, worker = worker_setup
    FakeCodex.disconnect = True
    task = await run_task(store, worker)
    assert task["state"] == "needs_reconciliation"
    assert claim(store) is None
    await worker.http.aclose()


async def test_network_loss_durable_final_and_restart_no_reexecution(worker_setup):
    store, worker = worker_setup
    row = store.submit("hello", "demo", "key")
    task = claim(store)
    worker.journal.save(task)
    original = worker.http

    class Offline:
        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    worker.http = Offline()
    await worker.execute(task)
    with pytest.raises(httpx.ConnectError):
        await worker.flush()
    assert worker.journal.pending()
    worker.http = original
    await worker.recover()
    assert store.get(row["id"])["state"] == "succeeded"
    assert FakeCodex.starts == 1 and worker.journal.tasks() == []
    await worker.http.aclose()


async def test_uncertain_worker_restart_never_replays(worker_setup):
    store, worker = worker_setup
    row = store.submit("hello", "demo", "key")
    task = claim(store)
    task["turn_start_intent"] = True
    worker.journal.save(task)
    await worker.recover()
    assert store.get(row["id"])["state"] == "needs_reconciliation"
    assert FakeCodex.starts == 0
    await worker.http.aclose()


async def test_local_approval_only(worker_setup):
    store, worker = worker_setup
    FakeCodex.approval_needed = True
    job = asyncio.create_task(run_task(store, worker))
    for _ in range(100):
        approvals = worker.journal.approvals()
        if approvals:
            break
        await asyncio.sleep(0.02)
    assert approvals
    restarted = Journal(worker.config.state)
    restarted.decide(approvals[0]["id"], "decline")
    assert (await job)["state"] == "succeeded"
    assert restarted.approvals() == []
    assert "harmless" not in str(store.events(store.tasks()["items"][0]["id"])["items"])
    await worker.http.aclose()


def test_workspace_symlinks_and_retarget(tmp_path):
    demo = tmp_path / "demo"
    demo.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(demo, target_is_directory=True)
    config = WorkerConfig(token="w" * 40, workspaces={"demo": str(alias)})
    with pytest.raises(ValueError):
        config.validate()
    config.workspaces = {"demo": str(demo)}
    config.validate()
    demo.rmdir()
    demo.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        config.resolve_workspace("demo")
    with pytest.raises(ValueError):
        config.resolve_workspace("../escape")
