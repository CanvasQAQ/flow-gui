from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
import json
import uuid

from .db import transaction


@dataclass(frozen=True)
class WorkItem:
    id: str
    unique_key: str
    work_type: str
    payload: dict[str, Any]
    attempt_count: int


class RetryWork(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: int = 5):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PermanentWorkError(RuntimeError):
    """A recorded failure that requires user or developer action."""


class DurableWorkQueue:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def enqueue(self, unique_key: str, work_type: str, payload: dict[str, Any]) -> str:
        with transaction(self.database_path) as connection:
            existing = connection.execute(
                "SELECT id FROM work_items WHERE unique_key = ?", (unique_key,)
            ).fetchone()
            if existing:
                return existing["id"]
            work_id = f"work_{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO work_items(id, unique_key, work_type, payload_json) VALUES (?, ?, ?, ?)",
                (work_id, unique_key, work_type, json.dumps(payload)),
            )
            return work_id

    def claim(self) -> WorkItem | None:
        with transaction(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT * FROM work_items
                WHERE status IN ('pending', 'retry') AND available_at <= CURRENT_TIMESTAMP
                ORDER BY created_at, id LIMIT 1
                """
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
                id=row["id"], unique_key=row["unique_key"], work_type=row["work_type"],
                payload=json.loads(row["payload_json"]), attempt_count=row["attempt_count"] + 1,
            )

    def recover_abandoned(self) -> int:
        """Return work interrupted by a Backend process exit to the retry queue."""
        with transaction(self.database_path) as connection:
            return connection.execute(
                """
                UPDATE work_items SET status = 'retry', available_at = CURRENT_TIMESTAMP,
                    claimed_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """
            ).rowcount

    def complete(self, work_id: str) -> None:
        with transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE work_items SET status = 'complete', finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (work_id,),
            )

    def fail(self, work_id: str, error: Exception, retry_after_seconds: int | None = None) -> None:
        status = "retry" if retry_after_seconds is not None else "failed"
        available_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds or 0)
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                UPDATE work_items SET status = ?, available_at = ?, last_error_json = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (
                    status, available_at.isoformat(),
                    json.dumps({"type": type(error).__name__, "message": str(error)}), work_id,
                ),
            )


class WorkProcessor:
    def __init__(
        self, queue: DurableWorkQueue, handlers: Mapping[str, Callable[[dict], None]],
        max_attempts: int = 3,
    ):
        self.queue = queue
        self.handlers = handlers
        self.max_attempts = max_attempts

    def tick(self) -> bool:
        item = self.queue.claim()
        if item is None:
            return False
        handler = self.handlers.get(item.work_type)
        if handler is None:
            self.queue.fail(item.id, KeyError(f"no handler for {item.work_type}"))
            return True
        try:
            handler(item.payload)
            self.queue.complete(item.id)
        except RetryWork as exc:
            self.queue.fail(item.id, exc, exc.retry_after_seconds)
        except PermanentWorkError as exc:
            self.queue.fail(item.id, exc)
        except Exception as exc:
            retry_after = min(60, 2 ** item.attempt_count) if item.attempt_count < self.max_attempts else None
            self.queue.fail(item.id, exc, retry_after)
        return True
