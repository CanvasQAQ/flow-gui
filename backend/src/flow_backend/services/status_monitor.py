"""Bounded, wall-clock driven status evidence monitoring.

Persistence is expressed as a protocol so the DB migration/repository layer can
implement leases without coupling filesystem I/O to a SQLite transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable, Protocol, Sequence
import json
import time
import uuid

from ..db import connect, transaction
from ..ports import SchedulerSnapshotReader


@dataclass(frozen=True)
class StatusCandidate:
    attempt_id: str
    status_path: Path
    job_name: str
    array_index: int
    previous_mtime_ns: int | None = None
    previous_size: int | None = None
    due_at: datetime | None = None


@dataclass(frozen=True)
class StatusFingerprint:
    mtime_ns: int
    size: int


class StatusMonitorStore(Protocol):
    def claim_due(
        self,
        *,
        limit: int,
        now: datetime,
        lease_owner: str,
        lease_until: datetime,
    ) -> Sequence[StatusCandidate]: ...

    def record_observation(
        self,
        candidate: StatusCandidate,
        fingerprint: StatusFingerprint,
        document: dict[str, Any] | None,
        *,
        observed_at: datetime,
        next_check_at: datetime,
    ) -> None: ...

    def record_failure(
        self,
        candidate: StatusCandidate,
        error: Exception,
        *,
        observed_at: datetime,
        next_check_at: datetime,
    ) -> None: ...


class SnapshotIdentityStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, identity: str | None) -> None: ...


class MemorySnapshotIdentityStore:
    def __init__(self) -> None:
        self.identity: str | None = None

    def load(self) -> str | None:
        return self.identity

    def save(self, identity: str | None) -> None:
        self.identity = identity


class SqliteSnapshotIdentityStore:
    """Persists the last fully-consumed Parquet identity across restarts."""

    def __init__(self, database_path: Path, key: str = "scheduler_snapshot_identity"):
        self.database_path = database_path
        self.key = key

    def load(self) -> str | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT value_json FROM runtime_state WHERE key = ?", (self.key,)
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row["value_json"])
        return value if isinstance(value, str) else None

    def save(self, identity: str | None) -> None:
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO runtime_state(key, value_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (self.key, json.dumps(identity)),
            )


class SqliteStatusMonitorStore:
    """SQLite lease/fingerprint implementation for exact Case status paths.

    The lease transaction only selects paths and reserves rows.  Network file
    operations happen later in :class:`StatusMonitor`, outside the transaction.
    """

    ACTIVE_STATUSES = ("pending", "running", "status_unknown", "copyback_waiting")

    def __init__(
        self,
        database_path: Path,
        on_observation: Callable[[str, dict[str, Any] | None], None] | None = None,
    ):
        self.database_path = database_path
        self.on_observation = on_observation

    def claim_due(
        self,
        *,
        limit: int,
        now: datetime,
        lease_owner: str,
        lease_until: datetime,
    ) -> Sequence[StatusCandidate]:
        now_text = _sqlite_time(now)
        placeholders = ",".join("?" for _ in self.ACTIVE_STATUSES)
        with transaction(self.database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT ea.id AS attempt_id, ea.array_index, ea.status_mtime_ns,
                       ea.status_size, ea.next_check_at,
                       t.case_path, b.unique_job_name
                FROM execution_attempts ea
                JOIN testcases t ON t.id = ea.testcase_id
                JOIN batches b ON b.id = ea.batch_id
                WHERE ea.status IN ({placeholders})
                  AND (ea.next_check_at IS NULL OR ea.next_check_at <= ?)
                  AND (ea.monitor_lease_until IS NULL OR ea.monitor_lease_until < ?)
                ORDER BY COALESCE(ea.next_check_at, ea.created_at), ea.id
                LIMIT ?
                """,
                (*self.ACTIVE_STATUSES, now_text, now_text, limit),
            ).fetchall()
            if not rows:
                return ()
            ids = [row["attempt_id"] for row in rows]
            id_placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"""
                UPDATE execution_attempts
                SET monitor_lease_owner = ?, monitor_lease_until = ?,
                    updated_at = updated_at
                WHERE id IN ({id_placeholders})
                  AND (monitor_lease_until IS NULL OR monitor_lease_until < ?)
                """,
                (lease_owner, _sqlite_time(lease_until), *ids, now_text),
            )
            # BEGIN IMMEDIATE serializes claimers.  The ownership condition also
            # makes the returned set explicit if this is later ported to another DB.
            owned = {
                row["id"]
                for row in connection.execute(
                    f"SELECT id FROM execution_attempts WHERE id IN ({id_placeholders}) AND monitor_lease_owner = ?",
                    (*ids, lease_owner),
                )
            }
        return tuple(
            StatusCandidate(
                attempt_id=row["attempt_id"],
                status_path=Path(row["case_path"]) / "status.json",
                job_name=row["unique_job_name"],
                array_index=int(row["array_index"] or 1),
                previous_mtime_ns=row["status_mtime_ns"],
                previous_size=row["status_size"],
                due_at=_parse_sqlite_time(row["next_check_at"]),
            )
            for row in rows
            if row["attempt_id"] in owned
        )

    def record_observation(
        self,
        candidate: StatusCandidate,
        fingerprint: StatusFingerprint,
        document: dict[str, Any] | None,
        *,
        observed_at: datetime,
        next_check_at: datetime,
    ) -> None:
        mapped_state = _attempt_state(document) if document is not None else None
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """SELECT ea.evidence_json, ea.testcase_id, t.algorithm_call_id
                   FROM execution_attempts ea JOIN testcases t ON t.id = ea.testcase_id
                   WHERE ea.id = ?""",
                (candidate.attempt_id,),
            ).fetchone()
            if row is None:
                return
            evidence = json.loads(row["evidence_json"] or "{}")
            evidence["statusPath"] = str(candidate.status_path)
            evidence["statusFingerprint"] = {
                "mtimeNs": fingerprint.mtime_ns,
                "size": fingerprint.size,
            }
            if document is not None:
                evidence["status"] = document
            if mapped_state is None:
                connection.execute(
                    """
                    UPDATE execution_attempts
                    SET evidence_json = ?, status_mtime_ns = ?, status_size = ?,
                        next_check_at = ?, last_checked_at = ?,
                        monitor_lease_owner = NULL, monitor_lease_until = NULL,
                        monitor_error_json = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        json.dumps(evidence), fingerprint.mtime_ns, fingerprint.size,
                        _sqlite_time(next_check_at), _sqlite_time(observed_at),
                        candidate.attempt_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE execution_attempts
                    SET status = ?, evidence_json = ?, status_mtime_ns = ?, status_size = ?,
                        next_check_at = ?, last_checked_at = ?,
                        scratch_path = COALESCE(?, scratch_path),
                        return_path = COALESCE(?, return_path),
                        execution_host = COALESCE(?, execution_host),
                        copyback_error_json = ?,
                        monitor_lease_owner = NULL, monitor_lease_until = NULL,
                        monitor_error_json = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        mapped_state, json.dumps(evidence), fingerprint.mtime_ns,
                        fingerprint.size, _sqlite_time(next_check_at),
                        _sqlite_time(observed_at), document.get("scratchPath"),
                        document.get("returnPath"), document.get("hostname"),
                        json.dumps(document.get("copybackError")) if document.get("copybackError") else None,
                        candidate.attempt_id,
                    ),
                )
                connection.execute(
                    "UPDATE testcases SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (mapped_state, row["testcase_id"]),
                )
        if self.on_observation is not None:
            self.on_observation(candidate.attempt_id, document)

    def record_failure(
        self,
        candidate: StatusCandidate,
        error: Exception,
        *,
        observed_at: datetime,
        next_check_at: datetime,
    ) -> None:
        diagnostic = json.dumps({"type": type(error).__name__, "message": str(error)})
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                UPDATE execution_attempts
                SET next_check_at = ?, last_checked_at = ?,
                    monitor_lease_owner = NULL, monitor_lease_until = NULL,
                    monitor_error_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    _sqlite_time(next_check_at), _sqlite_time(observed_at),
                    diagnostic, candidate.attempt_id,
                ),
            )

    def active_job_names(self) -> Sequence[str]:
        placeholders = ",".join("?" for _ in self.ACTIVE_STATUSES)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                f"""SELECT DISTINCT b.unique_job_name
                    FROM execution_attempts ea JOIN batches b ON b.id = ea.batch_id
                    WHERE ea.status IN ({placeholders}) ORDER BY b.unique_job_name""",
                self.ACTIVE_STATUSES,
            ).fetchall()
        return tuple(row["unique_job_name"] for row in rows)

    def record_snapshot(self, identity: str, observations: Sequence[dict[str, Any]]) -> None:
        """Store auxiliary scheduler evidence without treating it as completion proof."""
        with transaction(self.database_path) as connection:
            for observation in observations:
                job_name = observation.get("jobName")
                array_index = int(observation.get("arrayIndex") or 1)
                row = connection.execute(
                    """
                    SELECT b.id AS batch_id, ea.id AS attempt_id
                    FROM batches b JOIN execution_attempts ea ON ea.batch_id = b.id
                    WHERE b.unique_job_name = ? AND COALESCE(ea.array_index, 1) = ?
                    """,
                    (job_name, array_index),
                ).fetchone()
                if row is None:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO scheduler_observations(
                        batch_id, attempt_id, snapshot_identity, observed_status, raw_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["batch_id"], row["attempt_id"], identity,
                        observation.get("status"), json.dumps(observation),
                    ),
                )

class SqliteRuntimeHealthSink:
    """Publishes the compact system-level checker state consumed by the UI."""

    def __init__(self, database_path: Path, component_name: str = "status_checker"):
        self.database_path = database_path
        self.component_name = component_name

    def __call__(self, health: dict[str, Any]) -> None:
        detail = {
            "checked": health["checked"],
            "changed": health["changed"],
            "unchanged": health["unchanged"],
            "failed": health["failed"],
            "lastChunkSeconds": health["lastChunkSeconds"],
            "snapshotQueries": health["snapshotQueries"],
        }
        error = {"message": health["lastError"]} if health.get("lastError") else None
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO runtime_components(
                    name,state,detail_json,processed_count,started_at,last_heartbeat_at,
                    last_success_at,error_json,updated_at
                ) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    state=excluded.state, detail_json=excluded.detail_json,
                    processed_count=excluded.processed_count,
                    started_at=excluded.started_at,
                    last_heartbeat_at=CURRENT_TIMESTAMP,
                    last_success_at=excluded.last_success_at,
                    error_json=excluded.error_json, updated_at=CURRENT_TIMESTAMP
                """,
                (
                    self.component_name,
                    health["state"],
                    json.dumps(detail),
                    health["checked"],
                    health["lastStartedAt"],
                    health["lastSuccessAt"],
                    json.dumps(error) if error else None,
                ),
            )


@dataclass
class StatusMonitorMetrics:
    state: str = "normal"
    checking: bool = False
    checked: int = 0
    changed: int = 0
    unchanged: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_error: str | None = None
    last_chunk_seconds: float | None = None
    oldest_due_at: datetime | None = None
    snapshot_checked_at: datetime | None = None
    snapshot_identity: str | None = None
    snapshot_queries: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "checking": self.checking,
            "checked": self.checked,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "consecutiveFailures": self.consecutive_failures,
            "lastStartedAt": _iso(self.last_started_at),
            "lastSuccessAt": _iso(self.last_success_at),
            "lastFinishedAt": _iso(self.last_finished_at),
            "lastError": self.last_error,
            "lastChunkSeconds": self.last_chunk_seconds,
            "oldestDueAt": _iso(self.oldest_due_at),
            "snapshotCheckedAt": _iso(self.snapshot_checked_at),
            "snapshotIdentity": self.snapshot_identity,
            "snapshotQueries": self.snapshot_queries,
        }


class StatusMonitor:
    def __init__(
        self,
        store: StatusMonitorStore,
        *,
        interval_seconds: int = 600,
        chunk_size: int = 250,
        lease_seconds: int = 120,
        retry_seconds: int = 600,
        snapshot_reader: SchedulerSnapshotReader | None = None,
        snapshot_identity_store: SnapshotIdentityStore | None = None,
        active_job_names: Callable[[], Sequence[str]] | None = None,
        snapshot_sink: Callable[[str, Sequence[dict[str, Any]]], None] | None = None,
        health_sink: Callable[[dict[str, Any]], None] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        if interval_seconds <= 0 or chunk_size <= 0 or lease_seconds <= 0:
            raise ValueError("monitor interval, chunk size and lease must be positive")
        self.store = store
        self.interval_seconds = interval_seconds
        self.chunk_size = chunk_size
        self.lease_seconds = lease_seconds
        self.retry_seconds = retry_seconds
        self.snapshot_reader = snapshot_reader
        self.snapshot_identity_store = snapshot_identity_store or MemorySnapshotIdentityStore()
        self.active_job_names = active_job_names
        self.snapshot_sink = snapshot_sink
        self.health_sink = health_sink
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lease_owner = f"status-monitor-{uuid.uuid4().hex}"
        self._run_lock = Lock()
        self._metric_lock = Lock()
        self._metrics = StatusMonitorMetrics(snapshot_identity=self.snapshot_identity_store.load())
        self._last_snapshot_poll: datetime | None = None

    def run_due_chunk(self) -> dict[str, Any]:
        """Check one bounded chunk; return immediately if another call is active."""
        if not self._run_lock.acquire(blocking=False):
            return self.health()
        started = self._now()
        started_clock = time.monotonic()
        with self._metric_lock:
            self._metrics.checking = True
            self._metrics.state = "checking"
            self._metrics.last_started_at = started
        try:
            failures_before = self._metrics.failed
            candidates = self.store.claim_due(
                limit=self.chunk_size,
                now=started,
                lease_owner=self._lease_owner,
                lease_until=started + timedelta(seconds=self.lease_seconds),
            )
            due = [item.due_at for item in candidates if item.due_at is not None]
            with self._metric_lock:
                self._metrics.oldest_due_at = min(due) if due else None
            for candidate in candidates:
                self._check_candidate(candidate)
            self._poll_snapshot_if_due(started)
            with self._metric_lock:
                if self._metrics.failed == failures_before:
                    self._metrics.consecutive_failures = 0
                    self._metrics.last_error = None
                    self._metrics.last_success_at = self._now()
        except Exception as exc:
            with self._metric_lock:
                self._metrics.failed += 1
                self._metrics.consecutive_failures += 1
                self._metrics.last_error = str(exc)
        finally:
            finished = self._now()
            with self._metric_lock:
                self._metrics.checking = False
                self._metrics.last_finished_at = finished
                self._metrics.last_chunk_seconds = time.monotonic() - started_clock
                self._metrics.state = self._derive_state(finished)
            self._run_lock.release()
        health = self.health()
        if self.health_sink is not None:
            try:
                self.health_sink(health)
            except Exception as exc:
                with self._metric_lock:
                    self._metrics.failed += 1
                    self._metrics.consecutive_failures += 1
                    self._metrics.last_error = f"health publishing failed: {exc}"
                health = self.health()
        return health

    def health(self) -> dict[str, Any]:
        now = self._now()
        with self._metric_lock:
            self._metrics.state = "checking" if self._metrics.checking else self._derive_state(now)
            return self._metrics.as_dict()

    def _check_candidate(self, candidate: StatusCandidate) -> None:
        now = self._now()
        normal_next = now + timedelta(seconds=self.interval_seconds)
        try:
            stat = candidate.status_path.stat()
            fingerprint = StatusFingerprint(stat.st_mtime_ns, stat.st_size)
            unchanged = (
                candidate.previous_mtime_ns == fingerprint.mtime_ns
                and candidate.previous_size == fingerprint.size
            )
            document = None if unchanged else _read_status(candidate.status_path, candidate.attempt_id)
            self.store.record_observation(
                candidate,
                fingerprint,
                document,
                observed_at=now,
                next_check_at=normal_next,
            )
            with self._metric_lock:
                self._metrics.checked += 1
                if unchanged:
                    self._metrics.unchanged += 1
                else:
                    self._metrics.changed += 1
        except Exception as exc:
            self.store.record_failure(
                candidate,
                exc,
                observed_at=now,
                next_check_at=now + timedelta(seconds=self.retry_seconds),
            )
            with self._metric_lock:
                self._metrics.checked += 1
                self._metrics.failed += 1
                self._metrics.consecutive_failures += 1
                self._metrics.last_error = str(exc)

    def _poll_snapshot_if_due(self, now: datetime) -> None:
        if self.snapshot_reader is None:
            return
        if self._last_snapshot_poll is not None:
            age = (now - self._last_snapshot_poll).total_seconds()
            if age < self.interval_seconds:
                return
        self._last_snapshot_poll = now
        identity = self.snapshot_reader.snapshot_identity()
        with self._metric_lock:
            self._metrics.snapshot_checked_at = now
        previous = self.snapshot_identity_store.load()
        if identity is None or identity == previous:
            return
        names = tuple(dict.fromkeys(self.active_job_names() if self.active_job_names else ()))
        observations = self.snapshot_reader.read_jobs(names)
        if self.snapshot_sink is not None:
            self.snapshot_sink(identity, observations)
        # Save only after the query and sink both succeed.  A crash therefore
        # retries the same immutable snapshot instead of silently skipping it.
        self.snapshot_identity_store.save(identity)
        with self._metric_lock:
            self._metrics.snapshot_identity = identity
            self._metrics.snapshot_queries += 1

    def _derive_state(self, now: datetime) -> str:
        if self._metrics.checking:
            return "checking"
        if self._metrics.consecutive_failures:
            return "error"
        if self._metrics.oldest_due_at is not None:
            overdue = (now - self._metrics.oldest_due_at).total_seconds()
            if overdue > self.interval_seconds:
                return "delayed"
        if (
            self._metrics.last_chunk_seconds is not None
            and self._metrics.last_chunk_seconds > self.interval_seconds
        ):
            return "delayed"
        return "normal"


def _read_status(path: Path, attempt_id: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"status file must contain an object: {path}")
    if document.get("schemaVersion") != 1:
        raise ValueError(f"unsupported status schema: {path}")
    if document.get("attemptId") != attempt_id:
        raise ValueError(f"status file Attempt identity mismatch: {path}")
    return document


def _attempt_state(document: dict[str, Any] | None) -> str | None:
    if document is None:
        return None
    state = document.get("state")
    if state == "submit":
        return "pending"
    if state in {"staging_in", "running", "staging_out"}:
        return "running"
    if state == "complete":
        return "status_unknown" if document.get("missingExpectedFiles") else "succeeded"
    if state in {"failed", "simulation_failed"}:
        return "failed"
    if state in {"copyback_waiting", "status_unknown"}:
        return state
    return None


def _sqlite_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse_sqlite_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class StatusMonitorWorker:
    """A small independent scheduler; due times, not loop counts, select work."""

    def __init__(self, monitor: StatusMonitor, tick_seconds: float = 1.0):
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        self.monitor = monitor
        self.tick_seconds = tick_seconds
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="flow-status-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout_seconds: float = 10.0) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(max(timeout_seconds, 0))
        return self._thread is None or not self._thread.is_alive()

    def health(self) -> dict[str, Any]:
        return self.monitor.health()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.monitor.run_due_chunk()
            self._stop.wait(self.tick_seconds)
