import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import JobSpec, TERMINAL


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT UNIQUE, error TEXT, artifacts TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS queue_order ON jobs(state, created);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    at REAL NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        value = dict(row)
        value["spec"] = json.loads(value["spec"])
        value["artifacts"] = json.loads(value["artifacts"])
        value["cancel_requested"] = bool(value["cancel_requested"])
        return value

    def submit(self, spec: JobSpec, key: str | None = None):
        if key is not None and (not key.strip() or len(key) > 128):
            raise ValueError("Idempotency-Key must contain 1–128 characters")
        payload = json.dumps(spec.model_dump(), sort_keys=True)
        now, job_id = time.time(), uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if key is not None:
                previous = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
                if previous:
                    if previous["spec"] != payload:
                        raise Conflict("Idempotency-Key already belongs to a different request")
                    return self.decode(previous)
            db.execute("INSERT INTO jobs(id,spec,state,created,updated,idempotency_key) VALUES(?,?,?,?,?,?)",
                       (job_id, payload, "queued", now, now, key))
            db.execute("INSERT INTO events(job_id,at,state,message) VALUES(?,?,?,?)",
                       (job_id, now, "queued", "Accepted"))
        return self.get(job_id)

    def get(self, job_id):
        with self.connection() as db:
            return self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list(self, limit=50):
        with self.connection() as db:
            return [self.decode(r) for r in db.execute(
                "SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (min(max(limit, 1), 200),))]

    def events(self, job_id):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events WHERE job_id=? ORDER BY seq", (job_id,))]

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if not row:
                return None
            now = time.time()
            db.execute("UPDATE jobs SET state='preparing',updated=? WHERE id=?", (now, row["id"]))
            db.execute("INSERT INTO events(job_id,at,state,message) VALUES(?,?,?,?)",
                       (row["id"], now, "preparing", "Worker claimed job"))
        return self.get(row["id"])

    def transition(self, job_id, state, message, *, artifacts=None):
        allowed = {
            "preparing": {"recording", "failed", "cancelled", "interrupted"},
            "recording": {"rendering", "failed", "cancelled", "interrupted"},
            "rendering": {"succeeded", "failed", "cancelled", "interrupted"},
        }
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or state not in allowed.get(row["state"], set()):
                raise Conflict(f"Invalid job transition to {state}")
            if state == "succeeded" and row["cancel_requested"]:
                state, message = "cancelled", "Cancelled before publication of artifacts"
                artifacts = None
            now = time.time()
            db.execute("UPDATE jobs SET state=?,updated=?,error=?,artifacts=? WHERE id=?", (
                state, now, message if state in {"failed", "interrupted"} else None,
                json.dumps(artifacts or {}), job_id,
            ))
            db.execute("INSERT INTO events(job_id,at,state,message) VALUES(?,?,?,?)", (job_id, now, state, message))

    def cancel(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["state"] in TERMINAL:
                return self.decode(row)
            state = "cancelled" if row["state"] == "queued" else row["state"]
            now = time.time()
            db.execute("UPDATE jobs SET cancel_requested=1,state=?,updated=? WHERE id=?", (state, now, job_id))
            db.execute("INSERT INTO events(job_id,at,state,message) VALUES(?,?,?,?)",
                       (job_id, now, state, "Cancellation requested"))
        return self.get(job_id)

    def recover(self):
        # Only call while holding the machine's worker lock.
        with self.connection() as db:
            rows = db.execute("SELECT id FROM jobs WHERE state IN ('preparing','recording','rendering')").fetchall()
        for row in rows:
            self.transition(row["id"], "interrupted", "Worker stopped; partial output retained. Submit a new job.")
        return [r["id"] for r in rows]
