"""SQLite queue. BEGIN IMMEDIATE fences claims; uncertain work is never requeued."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
ACTIVE = {
    "leased",
    "running",
    "awaiting_local_approval",
    "cancel_requested",
    "needs_reconciliation",
}


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, config):
        self.config = config
        Path(config.database).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO migrations VALUES(1);
                CREATE TABLE IF NOT EXISTS tasks(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                  idempotency TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
                  instruction TEXT NOT NULL, workspace TEXT NOT NULL, timeout INTEGER NOT NULL,
                  previous_id TEXT, state TEXT NOT NULL, worker TEXT, attempt TEXT,
                  lease_until REAL, thread_id TEXT, turn_id TEXT,
                  result TEXT, error TEXT, final_digest TEXT, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state,seq);
                CREATE TABLE IF NOT EXISTS events(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                  event_id TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                  UNIQUE(task_id,event_id));
                CREATE INDEX IF NOT EXISTS event_task ON events(task_id,seq);
                INSERT OR IGNORE INTO migrations VALUES(2);
                CREATE TABLE IF NOT EXISTS history(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                  state TEXT NOT NULL, created REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS task_created AFTER INSERT ON tasks BEGIN
                  INSERT INTO history(task_id,state,created) VALUES(NEW.id,NEW.state,NEW.created);
                END;
                CREATE TRIGGER IF NOT EXISTS task_transition AFTER UPDATE OF state ON tasks
                WHEN OLD.state != NEW.state BEGIN
                  INSERT INTO history(task_id,state,created) VALUES(NEW.id,NEW.state,NEW.updated);
                END;
                CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, seen REAL NOT NULL,
                  protocol TEXT NOT NULL, version TEXT NOT NULL, workspaces TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.config.database, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def tx(self):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    @staticmethod
    def row(c, task_id):
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError("Task not found")
        result = dict(row)
        result["history"] = [
            dict(h)
            for h in c.execute(
                "SELECT seq,state,created FROM history WHERE task_id=? ORDER BY seq", (task_id,)
            )
        ]
        return result

    def get(self, task_id):
        with self.db() as c:
            return self.row(c, task_id)

    def submit(
        self, instruction, workspace, idempotency_key, timeout_seconds=None, previous_id=None
    ):
        if workspace not in self.config.workspaces:
            raise ValueError("Unauthorized workspace")
        if not instruction.strip() or len(instruction) > 32000:
            raise ValueError("Instruction must contain 1..32000 characters")
        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("Invalid idempotency key")
        timeout = 600 if timeout_seconds is None else timeout_seconds
        if not 1 <= timeout <= self.config.max_timeout:
            raise ValueError("Timeout outside server limit")
        digest = hashlib.sha256(
            json.dumps([instruction, workspace, timeout, previous_id]).encode()
        ).hexdigest()
        now = time.time()
        with self.tx() as c:
            existing = c.execute(
                "SELECT * FROM tasks WHERE idempotency=?", (idempotency_key,)
            ).fetchone()
            if existing:
                if existing["digest"] != digest:
                    raise Conflict("Idempotency key reused with different payload")
                return self.row(c, existing["id"])
            thread = None
            if previous_id:
                parent = self.row(c, previous_id)
                if parent["state"] not in TERMINAL or not parent["thread_id"]:
                    raise Conflict("Previous task must be terminal with a persisted thread")
                if parent["workspace"] != workspace:
                    raise Conflict("Continuation cannot change workspace")
                thread = parent["thread_id"]
                if c.execute(
                    "SELECT 1 FROM tasks WHERE thread_id=? AND state NOT IN ('succeeded','failed','cancelled','timed_out')",
                    (thread,),
                ).fetchone():
                    raise Conflict("Thread already has an active or queued continuation")
            if c.execute("SELECT count(*) FROM tasks").fetchone()[0] >= self.config.max_tasks:
                raise Conflict("Task retention limit reached; run maintenance")
            task_id = str(uuid.uuid4())
            c.execute(
                """INSERT INTO tasks(id,idempotency,digest,instruction,workspace,timeout,
              previous_id,state,thread_id,created,updated) VALUES(?,?,?,?,?,?,?,'queued',?,?,?)""",
                (
                    task_id,
                    idempotency_key,
                    digest,
                    instruction,
                    workspace,
                    timeout,
                    previous_id,
                    thread,
                    now,
                    now,
                ),
            )
            return self.row(c, task_id)

    def continue_task(self, task_id, instruction, idempotency_key, timeout_seconds=None):
        parent = self.get(task_id)
        return self.submit(
            instruction, parent["workspace"], idempotency_key, timeout_seconds, task_id
        )

    @staticmethod
    def expire(c, now):
        c.execute(
            """UPDATE tasks SET state='needs_reconciliation',updated=?
          WHERE state IN ('leased','running','awaiting_local_approval','cancel_requested') AND lease_until<?""",
            (now, now),
        )

    def register(self, worker_id, protocol, version, workspaces):
        if worker_id != self.config.worker_id or protocol != "1":
            raise Conflict("Worker identity or protocol mismatch")
        if not set(workspaces).issubset(self.config.workspaces):
            raise ValueError("Worker advertised unknown workspace")
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO workers VALUES(?,?,?,?,?)",
                (worker_id, time.time(), protocol, version, json.dumps(workspaces)),
            )
        return {"protocol": "1", "lease_seconds": self.config.lease_seconds}

    def heartbeat(self, worker_id):
        with self.tx() as c:
            self.expire(c, time.time())
            c.execute("UPDATE workers SET seen=? WHERE id=?", (time.time(), worker_id))
        return {"ok": True}

    def claim(self, worker_id):
        with self.tx() as c:
            now = time.time()
            self.expire(c, now)
            worker = c.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
            if not worker or now - worker["seen"] > self.config.lease_seconds:
                raise Conflict("Worker must register and heartbeat")
            if c.execute(
                "SELECT 1 FROM tasks WHERE worker=? AND state NOT IN ('succeeded','failed','cancelled','timed_out')",
                (worker_id,),
            ).fetchone():
                return None
            allowed = json.loads(worker["workspaces"])
            for r in c.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY seq").fetchall():
                if r["workspace"] not in allowed:
                    continue
                c.execute(
                    "UPDATE tasks SET state='leased',worker=?,attempt=?,lease_until=?,updated=? WHERE id=?",
                    (worker_id, str(uuid.uuid4()), now + self.config.lease_seconds, now, r["id"]),
                )
                return self.row(c, r["id"])
        return None

    def fenced(self, c, task_id, worker, attempt):
        row = self.row(c, task_id)
        if row["worker"] != worker or row["attempt"] != attempt:
            raise Conflict("Attempt ownership mismatch")
        return row

    def renew(self, task_id, worker, attempt):
        with self.tx() as c:
            r = self.fenced(c, task_id, worker, attempt)
            if r["state"] not in TERMINAL:
                # Reconciliation remains explicit until a journaled final result arrives.
                c.execute(
                    "UPDATE tasks SET lease_until=?,updated=? WHERE id=?",
                    (time.time() + self.config.lease_seconds, time.time(), task_id),
                )
            return {"state": r["state"]}

    def progress(self, task_id, worker, attempt, state, thread_id=None, turn_id=None):
        if state not in ("running", "awaiting_local_approval"):
            raise ValueError("Invalid progress state")
        with self.tx() as c:
            r = self.fenced(c, task_id, worker, attempt)
            if r["state"] in TERMINAL:
                raise Conflict("Task already terminal")
            if r["thread_id"] and thread_id and r["thread_id"] != thread_id:
                raise Conflict("Thread identity cannot change")
            if r["turn_id"] and turn_id and r["turn_id"] != turn_id:
                raise Conflict("Turn identity cannot change")
            if r["state"] in ("cancel_requested", "needs_reconciliation"):
                state = r["state"]
            c.execute(
                "UPDATE tasks SET state=?,thread_id=coalesce(?,thread_id),turn_id=coalesce(?,turn_id),updated=? WHERE id=?",
                (state, thread_id, turn_id, time.time(), task_id),
            )
        return {"ok": True}

    def event(self, task_id, worker, attempt, event_id, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > self.config.max_event_bytes:
            raise ValueError("Event exceeds byte limit")
        with self.tx() as c:
            self.fenced(c, task_id, worker, attempt)
            old = c.execute(
                "SELECT payload FROM events WHERE task_id=? AND event_id=?", (task_id, event_id)
            ).fetchone()
            if old:
                if old[0] != encoded:
                    raise Conflict("Event ID reused with different payload")
                return {"ok": True}
            if (
                c.execute("SELECT count(*) FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
                >= self.config.max_events
            ):
                return {"ok": True, "dropped": "event_limit"}
            c.execute(
                "INSERT INTO events(task_id,event_id,payload,created) VALUES(?,?,?,?)",
                (task_id, event_id, encoded, time.time()),
            )
        return {"ok": True}

    def finish(self, task_id, worker, attempt, state, result="", error=None):
        if state not in TERMINAL | {"needs_reconciliation"}:
            raise ValueError("Invalid final state")
        if len(result) > self.config.max_result_chars or (error and len(error) > 4000):
            raise ValueError("Result exceeds limit")
        digest = hashlib.sha256(json.dumps([state, result, error]).encode()).hexdigest()
        with self.tx() as c:
            r = self.fenced(c, task_id, worker, attempt)
            if r["final_digest"]:
                if r["final_digest"] != digest:
                    raise Conflict("Conflicting final result")
                return {"ok": True}
            if r["state"] in TERMINAL:
                raise Conflict("Task terminal")
            c.execute(
                "UPDATE tasks SET state=?,result=?,error=?,final_digest=?,updated=? WHERE id=?",
                (state, result, error, digest, time.time(), task_id),
            )
        return {"ok": True}

    def cancel(self, task_id):
        with self.tx() as c:
            r = self.row(c, task_id)
            if r["state"] not in TERMINAL:
                state = "cancelled" if r["state"] == "queued" else "cancel_requested"
                if r["state"] == "needs_reconciliation":
                    raise Conflict("Reconciliation required; cannot assume execution stopped")
                c.execute(
                    "UPDATE tasks SET state=?,updated=? WHERE id=?", (state, time.time(), task_id)
                )
            return self.row(c, task_id)

    def tasks(self, after=0, limit=50, state=None):
        if limit < 1 or limit > 100 or after < 0:
            raise ValueError("Invalid pagination")
        with self.db() as c:
            rows = c.execute(
                "SELECT * FROM tasks WHERE seq>? AND (? IS NULL OR state=?) ORDER BY seq LIMIT ?",
                (after, state, state, limit),
            ).fetchall()
            return {
                "items": [dict(r) for r in rows],
                "next_cursor": rows[-1]["seq"] if rows else after,
            }

    def events(self, task_id, after=0, limit=50):
        if limit < 1 or limit > 100 or after < 0:
            raise ValueError("Invalid pagination")
        with self.db() as c:
            self.row(c, task_id)
            rows = c.execute(
                "SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, after, limit),
            ).fetchall()
            return {
                "items": [{**dict(r), "payload": json.loads(r["payload"])} for r in rows],
                "next_cursor": rows[-1]["seq"] if rows else after,
            }

    def status(self):
        with self.tx() as c:
            self.expire(c, time.time())
            worker = c.execute(
                "SELECT seen,version FROM workers WHERE id=?", (self.config.worker_id,)
            ).fetchone()
            return {
                "protocol": "1",
                "worker_online": bool(
                    worker and time.time() - worker["seen"] < self.config.lease_seconds
                ),
                "worker_version": worker["version"] if worker else None,
                "counts": dict(
                    c.execute("SELECT state,count(*) FROM tasks GROUP BY state").fetchall()
                ),
                "workspaces": list(self.config.workspaces),
            }

    def reconcile(self, task_id, worker, attempt, state):
        if state not in TERMINAL:
            raise ValueError("Reconciliation must resolve to a terminal state")
        with self.tx() as c:
            r = self.fenced(c, task_id, worker, attempt)
            if r["state"] != "needs_reconciliation":
                raise Conflict("Only uncertain tasks can be reconciled")
            c.execute(
                "UPDATE tasks SET state=?,final_digest=NULL,error=?,updated=? WHERE id=?",
                (state, "Manually reconciled locally; no execution replayed", time.time(), task_id),
            )
        return {"ok": True}

    def cleanup(self):
        with self.tx() as c:
            cutoff = time.time() - self.config.retention_days * 86400
            # Keep idempotency tombstones and thread lineage; purge instruction/results/events.
            c.execute(
                "DELETE FROM events WHERE task_id IN (SELECT id FROM tasks WHERE updated<? AND state IN ('succeeded','failed','cancelled','timed_out'))",
                (cutoff,),
            )
            result = c.execute(
                "UPDATE tasks SET instruction='[retained metadata only]',result=NULL,error=NULL WHERE updated<? AND state IN ('succeeded','failed','cancelled','timed_out') AND instruction!='[retained metadata only]'",
                (cutoff,),
            )
            return {"purged_payloads": result.rowcount}
