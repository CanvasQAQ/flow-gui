from pathlib import Path
from typing import Callable, TypeVar
import hashlib
import json
import sqlite3

from .db import transaction


T = TypeVar("T")


class IdempotencyConflict(ValueError):
    pass


def canonical_request_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def perform(
        self,
        key: str,
        operation_type: str,
        payload: object,
        operation: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, bool]:
        request_hash = canonical_request_hash(payload)
        with transaction(self.database_path) as connection:
            previous = connection.execute(
                "SELECT * FROM operation_requests WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if previous is not None:
                if previous["operation_type"] != operation_type or previous["request_hash"] != request_hash:
                    raise IdempotencyConflict("idempotency key was already used for a different request")
                if previous["status"] != "complete":
                    raise IdempotencyConflict("an operation with this idempotency key is still pending")
                return json.loads(previous["result_json"]), True

            connection.execute(
                """
                INSERT INTO operation_requests(idempotency_key, operation_type, request_hash, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (key, operation_type, request_hash),
            )
            result = operation(connection)
            serializable = json.loads(json.dumps(result, ensure_ascii=False))
            connection.execute(
                """
                UPDATE operation_requests SET status = 'complete', result_json = ?,
                    completed_at = CURRENT_TIMESTAMP WHERE idempotency_key = ?
                """,
                (json.dumps(serializable, ensure_ascii=False), key),
            )
            return serializable, False

