"""Durable job queue. PostgreSQL row locks support independent API/worker replicas."""
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (JSON, Column, Float, Integer, MetaData, String, Table,
                        create_engine, delete, event, func, insert, or_, select, update)

ACTIVE = ("uploading", "queued", "running")
TERMINAL = ("complete", "partial", "failed", "cancelled")
metadata = MetaData()
jobs = Table("jobs", metadata,
    Column("id", String(64), primary_key=True), Column("status", String(20), nullable=False, index=True),
    Column("queue", String(32), nullable=False, index=True), Column("created", Float, nullable=False, index=True),
    Column("owner", String(128)), Column("lease_until", Float, index=True),
    Column("attempt", Integer, nullable=False, default=0), Column("data", JSON, nullable=False))
workers = Table("workers", metadata, Column("id", String(128), primary_key=True),
                Column("seen", Float, nullable=False), Column("data", JSON, nullable=False))
gate = Table("queue_gate", metadata, Column("id", Integer, primary_key=True))
schema = Table("schema_version", metadata, Column("version", Integer, primary_key=True))


def now():
    return datetime.now(timezone.utc).isoformat()


class QueueFull(Exception):
    pass


class LeaseLost(Exception):
    pass


class Database:
    def __init__(self, url, capacity=32, lease_seconds=120, max_attempts=3):
        self.capacity, self.lease_seconds, self.max_attempts = capacity, lease_seconds, max_attempts
        self.engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2, pool_timeout=10,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {
                "connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=5000"})
        self.sqlite = self.engine.dialect.name == "sqlite"
        if self.sqlite:
            @event.listens_for(self.engine, "connect")
            def pragmas(connection, record):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
        with self.engine.begin() as conn:
            if not self.sqlite:
                conn.exec_driver_sql("SELECT pg_advisory_xact_lock(7432049)")
            metadata.create_all(conn)
            version = conn.execute(select(schema.c.version)).scalar()
            if version not in (None, 1):
                raise RuntimeError("Unsupported database schema; migrate before starting")
            if version is None:
                conn.execute(insert(schema).values(version=1))
            if conn.execute(select(gate.c.id)).first() is None:
                conn.execute(insert(gate).values(id=1))

    @contextmanager
    def transaction(self, serialize=False):
        with self.engine.connect() as conn:
            if self.sqlite:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            elif serialize:
                conn.execute(select(gate).with_for_update()).first()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def reserve(self, record):
        with self.transaction(serialize=True) as conn:
            count = conn.execute(select(func.count()).select_from(jobs).where(jobs.c.status.in_(ACTIVE))).scalar_one()
            if count >= self.capacity:
                raise QueueFull("Job queue is full; retry after a job finishes")
            conn.execute(insert(jobs).values(id=record["id"], status=record["status"], queue=record["queue"],
                created=time.time(), attempt=0, data=record))

    def get(self, job_id):
        with self.engine.connect() as conn:
            return conn.execute(select(jobs.c.data).where(jobs.c.id == job_id)).scalar_one_or_none()

    def update(self, job_id, owner=None, **values):
        with self.transaction() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().first()
            if row is None:
                raise KeyError(job_id)
            if owner is not None and (row["owner"] != owner or row["status"] != "running" or row["lease_until"] < time.time()):
                raise LeaseLost(job_id)
            data = dict(row["data"], **values, updated_at=now())
            fields = {"data": data, "status": data["status"]}
            if data["status"] in TERMINAL:
                fields.update(owner=None, lease_until=None)
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(**fields))
            return data

    def peek(self, queues):
        """Read the FIFO candidate without starting it or consuming an attempt."""
        with self.engine.connect() as conn:
            return conn.execute(select(jobs.c.data).where(jobs.c.status == "queued", jobs.c.queue.in_(queues))
                .order_by(jobs.c.created, jobs.c.id).limit(1)).scalar_one_or_none()

    def claim(self, owner, queues, resource_domain=None, max_parallel=1, fail_on_worker_loss=False, admit=True, expected_id=None):
        # Serialize admission across replicas; the queue remains durable while a
        # host/GPU's shared inference slots are occupied.
        with self.transaction(serialize=True) as conn:
            expired = conn.execute(select(jobs).where(jobs.c.status == "running", jobs.c.lease_until < time.time())
                                   .with_for_update(skip_locked=True)).mappings().all()
            for row in expired:
                exhausted = row["attempt"] >= self.max_attempts or row["data"].get("fail_on_worker_loss", False)
                data = dict(row["data"], status="failed" if exhausted else "queued", stage="lease expired",
                            error="Worker heartbeat expired; inspect memory/container events before explicit retry" if exhausted else "Worker heartbeat expired", updated_at=now())
                if exhausted:
                    data["finished_at"] = now()
                conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(
                    status=data["status"], owner=None, lease_until=None, data=data))
            # Recover abandoned ingestion reservations; they never become runnable.
            abandoned = conn.execute(select(jobs).where(jobs.c.status == "uploading", jobs.c.created < time.time() - 7200)
                                     .with_for_update(skip_locked=True)).mappings().all()
            for row in abandoned:
                data = dict(row["data"], status="failed", stage="failed", error="Input ingestion expired", finished_at=now())
                conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(status="failed", data=data))
            if not admit:
                return None
            if resource_domain:
                owners = set(conn.execute(select(jobs.c.owner).where(jobs.c.status == "running",
                    jobs.c.data["resource_domain"].as_string() == resource_domain)).scalars())
                # A cancelled job may still be inside a native inference call.
                # Keep its slot until the worker actually drains or disappears.
                busy = conn.execute(select(workers.c.id, workers.c.data).where(
                    workers.c.seen > time.time() - self.lease_seconds,
                    workers.c.data["resource_domain"].as_string() == resource_domain)).all()
                owners.update(worker_id for worker_id, data in busy if data.get("job_id"))
                if len(owners) >= max_parallel:
                    return None
            row = conn.execute(select(jobs).where(jobs.c.status == "queued", jobs.c.queue.in_(queues))
                .order_by(jobs.c.created, jobs.c.id).limit(1).with_for_update(skip_locked=True)).mappings().first()
            if row is None:
                return None
            if expected_id is not None and row["id"] != expected_id:
                return None
            attempt = row["attempt"] + 1
            data = dict(row["data"], status="running", stage="starting", started_at=now(),
                        updated_at=now(), worker_id=owner, attempt=attempt, error=None,
                        resource_domain=resource_domain, fail_on_worker_loss=fail_on_worker_loss)
            conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(status="running", owner=owner,
                lease_until=time.time() + self.lease_seconds, attempt=attempt, data=data))
            if resource_domain:
                worker_data = conn.execute(select(workers.c.data).where(workers.c.id == owner)).scalar_one_or_none()
                values = dict(worker_data or {"hostname": owner, "queues": queues, "device": "starting", "model": "starting",
                    "memory_mb": 0, "cpu_percent": 0, "cpu_threads": 0, "stopping": False},
                    job_id=row["id"], resource_domain=resource_domain)
                if worker_data is None:
                    conn.execute(insert(workers).values(id=owner, seen=time.time(), data=values))
                else:
                    conn.execute(update(workers).where(workers.c.id == owner).values(seen=time.time(), data=values))
            return data

    def heartbeat(self, worker_id, data, job_id=None):
        with self.transaction(serialize=True) as conn:
            # Lock ownership updates with completion/reaping so expired workers cannot renew.
            if job_id:
                renewed = conn.execute(update(jobs).where(jobs.c.id == job_id, jobs.c.owner == worker_id,
                    jobs.c.status == "running", jobs.c.lease_until >= time.time())
                    .values(lease_until=time.time() + self.lease_seconds)).rowcount
                if not renewed:
                    raise LeaseLost(job_id)
            exists = conn.execute(select(workers.c.id).where(workers.c.id == worker_id)).first()
            if exists:
                conn.execute(update(workers).where(workers.c.id == worker_id).values(seen=time.time(), data=data))
            else:
                conn.execute(insert(workers).values(id=worker_id, seen=time.time(), data=data))

    def cancel(self, job_id):
        with self.transaction() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().first()
            if row is None:
                raise KeyError(job_id)
            if row["status"] not in ("queued", "running"):
                raise ValueError("Only queued or running jobs can be cancelled")
            data = dict(row["data"], status="cancelled", stage="cancelled", finished_at=now(), updated_at=now())
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(status="cancelled", owner=None, lease_until=None, data=data))
            return data

    def snapshot(self, limit=20, offset=0, status=None, search=""):
        with self.engine.connect() as conn:
            counts = dict(conn.execute(select(jobs.c.status, func.count()).group_by(jobs.c.status)).all())
            live = conn.execute(select(jobs.c.data).where(jobs.c.status.in_(ACTIVE)).order_by(jobs.c.created)).scalars().all()
            condition = jobs.c.status == status if status else jobs.c.status.in_(TERMINAL)
            # Extract JSON strings before matching, so escaped Unicode filenames
            # work consistently on PostgreSQL and SQLite.
            if search:
                condition = condition & or_(jobs.c.data["source_file"].as_string().icontains(search, autoescape=True),
                                             jobs.c.id.icontains(search, autoescape=True))
            total = conn.execute(select(func.count()).select_from(jobs).where(condition)).scalar_one()
            history = conn.execute(select(jobs.c.data).where(condition).order_by(jobs.c.created.desc(), jobs.c.id.desc())
                                   .offset(offset).limit(limit)).scalars().all()
            worker_rows = conn.execute(select(workers).where(workers.c.seen > time.time() - 86400)).mappings().all()
        worker_data = [dict(r["data"], id=r["id"], online=r["seen"] > time.time() - 30,
                            last_seen_seconds_ago=round(time.time() - r["seen"], 1)) for r in worker_rows]
        return {"running": [r for r in live if r["status"] == "running"],
                "queued": [dict(r, queue_position=i) for i, r in enumerate((r for r in live if r["status"] == "queued"), 1)],
                "uploading": [r for r in live if r["status"] == "uploading"],
                "history": history, "total_history": total, "limit": limit, "offset": offset,
                "counts": {s: counts.get(s, 0) for s in (*ACTIVE, *TERMINAL)},
                "capacity": self.capacity, "available_slots": max(0, self.capacity - sum(counts.get(s, 0) for s in ACTIVE)),
                "workers": worker_data, "database": self.engine.dialect.name}

    def import_legacy(self, root):
        imported = 0
        for file in sorted(Path(root).glob("*/job.json")):
            record = json.loads(file.read_text(encoding="utf-8"))
            if self.get(record["id"]):
                continue
            if record["status"] in ACTIVE:
                record.update(status="failed", stage="failed", error="Legacy unfinished job imported; retry explicitly")
            record.setdefault("queue", "default")
            record.setdefault("options", {})
            record.setdefault("created_at", now())
            record["legacy_dir"] = str(file.parent.resolve())
            created = datetime.fromisoformat(record["created_at"]).timestamp()
            with self.transaction() as conn:
                conn.execute(insert(jobs).values(id=record["id"], status=record["status"], queue=record["queue"],
                    created=created, attempt=0, data=record))
            imported += 1
        return imported

    def close(self):
        self.engine.dispose()
