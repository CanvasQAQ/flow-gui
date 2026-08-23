"""Independent durable worker lanes for blocking workflow integrations.

The work queue remains the durable source of truth.  A lane only claims the
work types assigned to it, so a slow scheduler submission cannot occupy the
Postprocess or normal Workflow worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable, Mapping, Sequence
import json

from .db import transaction
from .work_queue import PermanentWorkError, RetryWork, WorkItem


@dataclass(frozen=True)
class WorkerLaneConfig:
    name: str
    work_types: tuple[str, ...]
    concurrency: int = 1
    idle_seconds: float = 0.5
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.name or not self.work_types:
            raise ValueError("a worker lane needs a name and at least one work type")
        if self.concurrency < 1:
            raise ValueError("lane concurrency must be positive")
        if self.idle_seconds <= 0:
            raise ValueError("idle_seconds must be positive")


@dataclass
class WorkerLaneMetrics:
    active: int = 0
    completed: int = 0
    failed: int = 0
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "completed": self.completed,
            "failed": self.failed,
            "lastStartedAt": self.last_started_at,
            "lastFinishedAt": self.last_finished_at,
            "lastError": self.last_error,
        }


class DurableLaneQueue:
    """Claims only selected work types using the existing durable work table."""

    def __init__(self, database_path: Path, work_types: Sequence[str]):
        self.database_path = database_path
        self.work_types = tuple(dict.fromkeys(work_types))
        if not self.work_types:
            raise ValueError("work_types cannot be empty")

    def claim(self) -> WorkItem | None:
        placeholders = ",".join("?" for _ in self.work_types)
        with transaction(self.database_path) as connection:
            row = connection.execute(
                f"""
                SELECT * FROM work_items
                WHERE status IN ('pending', 'retry')
                  AND available_at <= CURRENT_TIMESTAMP
                  AND work_type IN ({placeholders})
                ORDER BY created_at, id LIMIT 1
                """,
                self.work_types,
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE work_items SET status = 'running', claimed_at = CURRENT_TIMESTAMP,
                    attempt_count = attempt_count + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('pending', 'retry')
                """,
                (row["id"],),
            ).rowcount
            if changed != 1:
                return None
            return WorkItem(
                id=row["id"],
                unique_key=row["unique_key"],
                work_type=row["work_type"],
                payload=json.loads(row["payload_json"]),
                attempt_count=row["attempt_count"] + 1,
            )

    def complete(self, work_id: str) -> None:
        with transaction(self.database_path) as connection:
            connection.execute(
                """UPDATE work_items SET status = 'complete', finished_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (work_id,),
            )

    def fail(self, item: WorkItem, error: Exception, retry_seconds: int | None) -> None:
        status = "retry" if retry_seconds is not None else "failed"
        available_at = datetime.now(timezone.utc) + timedelta(seconds=retry_seconds or 0)
        with transaction(self.database_path) as connection:
            connection.execute(
                """UPDATE work_items SET status = ?, available_at = ?, last_error_json = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (
                    status,
                    available_at.strftime("%Y-%m-%d %H:%M:%S"),
                    json.dumps({"type": type(error).__name__, "message": str(error)}),
                    item.id,
                ),
            )


class RuntimeWorkerSupervisor:
    """Runs durable work lanes in independent threads.

    The class deliberately has no FastAPI dependency.  Electron/API lifecycle
    code can start it after recovery and stop it during Backend draining.
    """

    def __init__(
        self,
        database_path: Path,
        handlers: Mapping[str, Callable[[dict[str, Any]], None]],
        lanes: Sequence[WorkerLaneConfig],
    ):
        claimed: set[str] = set()
        for lane in lanes:
            duplicate = claimed.intersection(lane.work_types)
            if duplicate:
                raise ValueError(f"work types assigned to multiple lanes: {sorted(duplicate)}")
            claimed.update(lane.work_types)
            missing = set(lane.work_types).difference(handlers)
            if missing:
                raise ValueError(f"handlers missing for lane {lane.name}: {sorted(missing)}")
        self.database_path = database_path
        self.handlers = dict(handlers)
        self.lanes = tuple(lanes)
        self._stop = Event()
        self._threads: list[Thread] = []
        self._metrics = {lane.name: WorkerLaneMetrics() for lane in lanes}
        self._metric_locks = {lane.name: Lock() for lane in lanes}

    @staticmethod
    def workflow_defaults() -> tuple[WorkerLaneConfig, ...]:
        return (
            WorkerLaneConfig("submission", ("submit_batch",), concurrency=1),
            WorkerLaneConfig("postprocess", ("run_postprocess",), concurrency=1),
            WorkerLaneConfig(
                "workflow", ("initialize_run_corner", "decide_stage"), concurrency=1
            ),
        )

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads):
            return
        self._stop.clear()
        self._threads = []
        for lane in self.lanes:
            for index in range(lane.concurrency):
                thread = Thread(
                    target=self._run_lane,
                    args=(lane,),
                    name=f"flow-{lane.name}-{index + 1}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def stop(self, timeout_seconds: float = 60.0) -> bool:
        self._stop.set()
        deadline = monotonic() + max(timeout_seconds, 0)
        for thread in self._threads:
            thread.join(max(0, deadline - monotonic()))
        return not any(thread.is_alive() for thread in self._threads)

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for lane in self.lanes:
            with self._metric_locks[lane.name]:
                result[lane.name] = self._metrics[lane.name].as_dict()
        return result

    def _run_lane(self, lane: WorkerLaneConfig) -> None:
        queue = DurableLaneQueue(self.database_path, lane.work_types)
        while not self._stop.is_set():
            try:
                item = queue.claim()
            except Exception as exc:
                self._record_loop_error(lane.name, exc)
                self._stop.wait(lane.idle_seconds)
                continue
            if item is None:
                self._stop.wait(lane.idle_seconds)
                continue
            self._mark_start(lane.name)
            error: Exception | None = None
            try:
                self.handlers[item.work_type](item.payload)
                queue.complete(item.id)
            except RetryWork as exc:
                error = exc
                self._safe_fail(queue, item, exc, exc.retry_after_seconds, lane.name)
            except PermanentWorkError as exc:
                error = exc
                self._safe_fail(queue, item, exc, None, lane.name)
            except Exception as exc:  # keep worker alive after an adapter failure
                error = exc
                retry = min(60, 2**item.attempt_count) if item.attempt_count < lane.max_attempts else None
                self._safe_fail(queue, item, exc, retry, lane.name)
            self._mark_finish(lane.name, error)

    def _safe_fail(
        self,
        queue: DurableLaneQueue,
        item: WorkItem,
        error: Exception,
        retry_seconds: int | None,
        lane_name: str,
    ) -> None:
        try:
            queue.fail(item, error, retry_seconds)
        except Exception as persistence_error:
            self._record_loop_error(lane_name, persistence_error)

    def _record_loop_error(self, lane_name: str, error: Exception) -> None:
        with self._metric_locks[lane_name]:
            metrics = self._metrics[lane_name]
            metrics.failed += 1
            metrics.last_error = str(error)

    def _mark_start(self, lane_name: str) -> None:
        with self._metric_locks[lane_name]:
            metrics = self._metrics[lane_name]
            metrics.active += 1
            metrics.last_started_at = datetime.now(timezone.utc).isoformat()

    def _mark_finish(self, lane_name: str, error: Exception | None) -> None:
        with self._metric_locks[lane_name]:
            metrics = self._metrics[lane_name]
            metrics.active -= 1
            metrics.last_finished_at = datetime.now(timezone.utc).isoformat()
            if error is None:
                metrics.completed += 1
                metrics.last_error = None
            else:
                metrics.failed += 1
                metrics.last_error = str(error)
