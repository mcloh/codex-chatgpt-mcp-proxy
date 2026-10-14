import concurrent.futures
import time
import pytest
from codex_chatgpt_mcp_proxy.store import Store, Conflict
from conftest import claim


def test_idempotency_and_payload_conflict(store):
    first = store.submit("hello", "demo", "key")
    assert store.submit("hello", "demo", "key")["id"] == first["id"]
    with pytest.raises(Conflict):
        store.submit("different", "demo", "key")
    with pytest.raises(ValueError):
        store.submit("hello", "/tmp", "another")
    with pytest.raises(ValueError):
        store.submit("hello", "demo", "another", 4000)


def test_concurrent_claim_is_atomic(store):
    store.submit("hello", "demo", "key")
    store.register("home", "1", "test", ["demo"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: store.claim("home"), range(8)))
    assert sum(r is not None for r in rows) == 1


def test_renew_expiry_fencing_and_final_retry(store):
    task = store.submit("hello", "demo", "key")
    r = claim(store)
    with pytest.raises(Conflict):
        store.renew(task["id"], "home", "wrong")
    store.progress(task["id"], "home", r["attempt"], "running", "thread", "turn")
    assert store.renew(task["id"], "home", r["attempt"])["state"] == "running"
    with store.tx() as c:
        c.execute("UPDATE tasks SET lease_until=?", (time.time() - 1,))
    assert store.claim("home") is None
    assert store.get(task["id"])["state"] == "needs_reconciliation"
    store.finish(task["id"], "home", r["attempt"], "succeeded", "done")
    store.finish(task["id"], "home", r["attempt"], "succeeded", "done")
    with pytest.raises(Conflict):
        store.finish(task["id"], "home", r["attempt"], "failed", "different")
    with pytest.raises(Conflict):
        store.progress(task["id"], "home", r["attempt"], "running")
    assert store.cancel(task["id"])["state"] == "succeeded"


def test_continuation_thread_serialization(store):
    task = store.submit("hello", "demo", "key")
    r = claim(store)
    with pytest.raises(Conflict):
        store.continue_task(task["id"], "continue", "key2")
    store.progress(task["id"], "home", r["attempt"], "running", "thread", "turn")
    store.finish(task["id"], "home", r["attempt"], "succeeded", "done")
    second = store.continue_task(task["id"], "continue", "key2")
    assert second["thread_id"] == "thread"
    with pytest.raises(Conflict):
        store.continue_task(task["id"], "again", "key3")
    assert claim(store)["id"] == second["id"]


def test_events_persistence_and_pagination(store, config):
    task = store.submit("hello", "demo", "key")
    r = claim(store)
    for i in range(4):
        store.event(task["id"], "home", r["attempt"], str(i), {"i": i})
    store.event(task["id"], "home", r["attempt"], "0", {"i": 0})
    with pytest.raises(Conflict):
        store.event(task["id"], "home", r["attempt"], "0", {"i": 99})
    restarted = Store(config)
    page = restarted.events(task["id"], limit=2)
    assert len(page["items"]) == 2
    page2 = restarted.events(task["id"], after=page["next_cursor"])
    assert [r["payload"]["i"] for r in page2["items"]] == [2, 3]
    assert restarted.get(task["id"])["attempt"] == r["attempt"]


def test_cancel_queued_running_and_terminal(store):
    queued = store.submit("hello", "demo", "key")
    assert store.cancel(queued["id"])["state"] == "cancelled"
    task = store.submit("hello", "demo", "key2")
    r = claim(store)
    assert store.cancel(task["id"])["state"] == "cancel_requested"
    store.progress(task["id"], "home", r["attempt"], "running")
    assert store.get(task["id"])["state"] == "cancel_requested"
    store.finish(task["id"], "home", r["attempt"], "cancelled")
    assert store.cancel(task["id"])["state"] == "cancelled"


def test_bounds_and_retention(store):
    task = store.submit("hello", "demo", "key")
    r = claim(store)
    with pytest.raises(ValueError):
        store.event(task["id"], "home", r["attempt"], "large", {"v": "x" * 20000})
    store.finish(task["id"], "home", r["attempt"], "succeeded", "done")
    with store.tx() as c:
        c.execute("UPDATE tasks SET updated=?", (time.time() - 86400 * 31,))
    assert store.cleanup()["purged_payloads"] == 1
    assert store.get(task["id"])["result"] is None


def test_manual_reconciliation_audit_and_release(store):
    task = store.submit("hello", "demo", "k")
    leased = claim(store)
    store.finish(task["id"], "home", leased["attempt"], "needs_reconciliation", error="uncertain")
    store.reconcile(task["id"], "home", leased["attempt"], "failed")
    assert store.get(task["id"])["state"] == "failed"
    assert [h["state"] for h in store.get(task["id"])["history"]] == [
        "queued",
        "leased",
        "needs_reconciliation",
        "failed",
    ]
    with pytest.raises(Conflict):
        store.reconcile(task["id"], "home", leased["attempt"], "failed")
    store.submit("next", "demo", "k2")
    assert claim(store) is not None
