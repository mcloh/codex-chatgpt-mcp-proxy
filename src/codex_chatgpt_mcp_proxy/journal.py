"""Durable worker outbox and local-only approvals."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Journal:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS active(id TEXT PRIMARY KEY, task TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, request TEXT NOT NULL, decision TEXT, done INTEGER DEFAULT 0);
            """)
        os.chmod(path, 0o600)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def save(self, task):
        with self.db() as c:
            c.execute("INSERT OR REPLACE INTO active VALUES(?,?)", (task["id"], json.dumps(task)))

    def tasks(self):
        with self.db() as c:
            return [json.loads(r["task"]) for r in c.execute("SELECT * FROM active")]

    def queue(self, path, body):
        with self.db() as c:
            c.execute("INSERT INTO outbox(path,body) VALUES(?,?)", (path, json.dumps(body)))

    def pending(self):
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM outbox ORDER BY seq LIMIT 100")]

    def ack(self, seq):
        with self.db() as c:
            c.execute("DELETE FROM outbox WHERE seq=?", (seq,))

    def remove(self, task_id):
        with self.db() as c:
            c.execute("DELETE FROM active WHERE id=?", (task_id,))
            c.execute("UPDATE approvals SET done=1 WHERE task_id=?", (task_id,))

    def approval(self, task_id, request):
        approval_id = str(uuid.uuid4())
        with self.db() as c:
            c.execute(
                "INSERT INTO approvals(id,task_id,request) VALUES(?,?,?)",
                (approval_id, task_id, json.dumps(request)),
            )
        return approval_id

    def approvals(self):
        with self.db() as c:
            return [
                {**dict(r), "request": json.loads(r["request"])}
                for r in c.execute("SELECT * FROM approvals WHERE done=0")
            ]

    def decide(self, approval_id, decision):
        if decision not in ("accept", "decline", "cancel"):
            raise ValueError("Only per-request decisions accepted")
        with self.db() as c:
            updated = c.execute(
                "UPDATE approvals SET decision=? WHERE id=? AND done=0 AND decision IS NULL",
                (decision, approval_id),
            )
            if not updated.rowcount:
                raise ValueError("Approval not pending")

    def decision(self, approval_id):
        with self.db() as c:
            r = c.execute(
                "SELECT decision FROM approvals WHERE id=? AND done=0", (approval_id,)
            ).fetchone()
            return r[0] if r else "decline"

    def done(self, approval_id):
        with self.db() as c:
            c.execute("UPDATE approvals SET done=1 WHERE id=?", (approval_id,))
