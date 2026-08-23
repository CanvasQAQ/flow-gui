from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Sequence
import json
import sqlite3
import threading
import uuid

from ..adapters.copyback import CopyBackError, CopyBackRequest, SshCopyBackAdapter
from ..batch import BatchItem, BatchManifest, attempt_return_path, read_execution_status, write_execution_status
from ..db import connect, transaction
from ..idempotency import IdempotencyStore
from ..work_queue import DurableWorkQueue
from ..workflow import algorithm_group_ready


class RecoveryError(ValueError):
    pass


@dataclass(frozen=True)
class CaseFilter:
    testcase_ids: tuple[str, ...] = ()
    corner_numbers: tuple[int, ...] = ()
    stage_keys: tuple[str, ...] = ()
    algorithm_call_numbers: tuple[int, ...] = ()
    statuses: tuple[str, ...] = ()


class CaseRecoveryService:
    def __init__(
        self, database_path: Path, simulator_command: Sequence[str],
        expected_files: Sequence[str], scheduler_settings: dict[str, Any],
        copyback_adapter: SshCopyBackAdapter | None = None,
    ):
        self.database_path = database_path
        self.simulator_command = tuple(simulator_command)
        self.expected_files = tuple(expected_files)
        self.scheduler_settings = scheduler_settings
        self.idempotency = IdempotencyStore(database_path)
        self.queue = DurableWorkQueue(database_path)
        self.copyback_adapter = copyback_adapter or SshCopyBackAdapter()
        self._copyback_lock = threading.Lock()
        self._copyback_attempts: set[str] = set()

    def validate_copyback_request(self, attempt_id: str) -> None:
        """Fast local validation used before queueing network copy work."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT testcase_id, status FROM execution_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            latest = connection.execute(
                """
                SELECT id FROM execution_attempts
                WHERE testcase_id = (SELECT testcase_id FROM execution_attempts WHERE id = ?)
                ORDER BY attempt_number DESC LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RecoveryError("unknown execution Attempt")
        if latest is None or latest["id"] != attempt_id:
            raise RecoveryError("copy-back is only allowed for the current Attempt")
        if row["status"] not in {"copyback_waiting", "status_unknown"}:
            raise RecoveryError(f"Attempt state does not allow copy-back: {row['status']}")

    def retry_copyback(self, attempt_id: str) -> dict:
        """Retry only stage-out for the current Attempt; never re-run HSPICE."""
        self.validate_copyback_request(attempt_id)
        with self._copyback_lock:
            if attempt_id in self._copyback_attempts:
                raise RecoveryError("copy-back is already running for this Attempt")
            self._copyback_attempts.add(attempt_id)
        try:
            return self._retry_copyback(attempt_id)
        finally:
            with self._copyback_lock:
                self._copyback_attempts.discard(attempt_id)

    def _retry_copyback(self, attempt_id: str) -> dict:
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT ea.id AS attempt_id, ea.status AS attempt_status, ea.scratch_path,
                       ea.return_path, ea.batch_id, ea.array_index, ea.testcase_id,
                       t.case_path, r.id AS run_id
                FROM execution_attempts ea JOIN testcases t ON t.id = ea.testcase_id
                JOIN stage_executions se ON se.id = t.stage_execution_id
                JOIN run_corners rc ON rc.id = se.run_corner_id
                JOIN runs r ON r.id = rc.run_id
                WHERE ea.id = ?
                """,
                (attempt_id,),
            ).fetchone()
            latest = connection.execute(
                "SELECT id FROM execution_attempts WHERE testcase_id = (SELECT testcase_id FROM execution_attempts WHERE id = ?) ORDER BY attempt_number DESC LIMIT 1",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RecoveryError("unknown execution Attempt")
        if latest is None or latest["id"] != attempt_id:
            raise RecoveryError("copy-back is only allowed for the current Attempt")
        if row["attempt_status"] not in {"copyback_waiting", "status_unknown"}:
            raise RecoveryError(f"Attempt state does not allow copy-back: {row['attempt_status']}")

        status_path = Path(row["case_path"]) / "status.json"
        try:
            status = read_execution_status(status_path, attempt_id)
        except Exception as exc:
            raise RecoveryError(f"current Attempt status cannot provide Scratch identity: {exc}") from exc
        if (
            status.get("state") not in {"copyback_waiting", "staging_out"}
            or status.get("simulatorExitCode") != 0
            or status.get("missingExpectedFiles")
        ):
            raise RecoveryError("copy-back requires evidence that HSPICE completed successfully")
        scratch_value = row["scratch_path"] or status.get("scratchPath")
        if not scratch_value:
            raise RecoveryError("current Attempt status has no Scratch path")
        synthetic_item = BatchItem(
            row["array_index"], row["testcase_id"], attempt_id, row["case_path"],
            str(Path(row["case_path"]) / "testcase.sp"), str(status_path), row["return_path"],
        )
        return_path = Path(row["return_path"]) if row["return_path"] else attempt_return_path(synthetic_item)
        base = {
            "batchId": row["batch_id"], "testcaseId": row["testcase_id"],
            "attemptId": attempt_id, "arrayIndex": row["array_index"],
            "startedAt": status.get("startedAt"), "scratchPath": scratch_value,
            "returnPath": str(return_path),
        }
        write_execution_status(status_path, state="staging_out", recovery=True, **base)
        try:
            result = self.copyback_adapter.copy(CopyBackRequest(
                attempt_id=attempt_id,
                scratch_path=Path(scratch_value),
                return_path=return_path,
            ))
        except CopyBackError as exc:
            error = {"code": exc.code, "message": str(exc), "detail": exc.detail}
            write_execution_status(
                status_path, state="copyback_waiting", recovery=True,
                copybackError=error, finishedAt=datetime.now(timezone.utc).isoformat(), **base,
            )
            with transaction(self.database_path) as connection:
                connection.execute(
                    """
                    UPDATE execution_attempts SET status = 'copyback_waiting',
                           scratch_path = ?, return_path = ?, copyback_error_json = ?,
                           updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (scratch_value, str(return_path), json.dumps(error), attempt_id),
                )
                connection.execute(
                    "UPDATE testcases SET status = 'copyback_waiting', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["testcase_id"],),
                )
                connection.execute(
                    "INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id, payload_json) VALUES (?, 'copyback_failed', 'execution_attempt', ?, ?)",
                    (row["run_id"], attempt_id, json.dumps(error)),
                )
            return {"attemptId": attempt_id, "status": "copyback_waiting", "error": error}

        write_execution_status(
            status_path, state="complete", recovery=True, hostname=result.hostname,
            simulatorExitCode=status.get("simulatorExitCode"),
            missingExpectedFiles=status.get("missingExpectedFiles", []),
            finishedAt=datetime.now(timezone.utc).isoformat(), **base,
        )
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                UPDATE execution_attempts SET status = 'succeeded', execution_host = ?,
                       scratch_path = ?, return_path = ?, copyback_error_json = NULL,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (result.hostname, scratch_value, str(return_path), attempt_id),
            )
            connection.execute(
                "UPDATE testcases SET status = 'succeeded', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["testcase_id"],),
            )
            connection.execute(
                "INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id, payload_json) VALUES (?, 'copyback_completed', 'execution_attempt', ?, ?)",
                (row["run_id"], attempt_id, json.dumps({"hostname": result.hostname})),
            )
        return {
            "attemptId": attempt_id, "status": "succeeded", "hostname": result.hostname,
            "returnPath": str(return_path),
        }

    def list_cases(self, run_id: str, filters: CaseFilter, limit: int = 500) -> list[dict]:
        clauses = ["r.id = ?"]
        values: list[Any] = [run_id]
        for column, selected in (
            ("t.id", filters.testcase_ids),
            ("c.corner_number", filters.corner_numbers),
            ("se.stage_key", filters.stage_keys),
            ("ac.call_number", filters.algorithm_call_numbers),
            ("t.status", filters.statuses),
        ):
            if selected:
                clauses.append(f"{column} IN ({','.join('?' for _ in selected)})")
                values.extend(selected)
        values.append(min(max(limit, 1), 5000))
        with connect(self.database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT t.id, t.name, t.status, t.case_path, t.sp_path,
                       c.corner_number, se.stage_key, ac.call_number,
                       ea.id AS attempt_id, ea.attempt_number, ea.status AS attempt_status,
                       ea.lsf_job_id, ea.array_index, b.id AS batch_id
                FROM testcases t JOIN algorithm_calls ac ON ac.id = t.algorithm_call_id
                JOIN stage_executions se ON se.id = t.stage_execution_id
                JOIN run_corners rc ON rc.id = se.run_corner_id JOIN runs r ON r.id = rc.run_id
                JOIN corners c ON c.id = rc.corner_id
                LEFT JOIN execution_attempts ea ON ea.testcase_id = t.id
                  AND ea.attempt_number = (SELECT MAX(x.attempt_number) FROM execution_attempts x WHERE x.testcase_id = t.id)
                LEFT JOIN batches b ON b.id = ea.batch_id
                WHERE {' AND '.join(clauses)}
                ORDER BY c.corner_number, se.created_at, ac.call_number, t.name LIMIT ?
                """,
                values,
            ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    def preview(
        self, run_id: str, action: Literal["resubmit", "ignore"], filters: CaseFilter,
        ttl_minutes: int = 15,
    ) -> dict:
        cases = self.list_cases(run_id, filters, limit=5000)
        if not cases:
            raise RecoveryError("no Testcases match this selection")
        token = f"preview_{uuid.uuid4().hex}"
        risks = {
            "active": sum(case["attempt_status"] in {"pending", "running"} for case in cases),
            "unknown": sum(case["attempt_status"] == "status_unknown" for case in cases),
            "completed": sum(case["attempt_status"] == "succeeded" for case in cases),
            "mayOverwrite": sum(case["attempt_status"] in {"pending", "running", "status_unknown", "succeeded"} for case in cases),
        }
        groups = {}
        for case in cases:
            key = (case["corner_number"], case["stage_key"], case["call_number"])
            groups.setdefault(key, 0)
            groups[key] += 1
        snapshot = {
            "testcases": [{"id": case["id"], "status": case["status"], "attemptStatus": case["attempt_status"]} for case in cases],
            "risks": risks,
            "groups": [
                {"cornerNumber": key[0], "stageKey": key[1], "algorithmCall": key[2], "count": count}
                for key, count in groups.items()
            ],
        }
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO recovery_previews(token, run_id, action, selection_json, snapshot_json, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token, run_id, action, json.dumps(asdict(filters)), json.dumps(snapshot), expires.isoformat()),
            )
        return {"token": token, "action": action, "count": len(cases), "expiresAt": expires.isoformat(), **snapshot}

    def confirm(self, token: str, idempotency_key: str) -> tuple[dict, bool]:
        with connect(self.database_path) as connection:
            preview = connection.execute(
                "SELECT * FROM recovery_previews WHERE token = ?", (token,)
            ).fetchone()
        if preview is None:
            raise RecoveryError("unknown recovery preview token")
        if datetime.fromisoformat(preview["expires_at"]) < datetime.now(timezone.utc):
            raise RecoveryError("recovery preview has expired")
        filters = CaseFilter(**{key: tuple(value) for key, value in json.loads(preview["selection_json"]).items()})
        current = self.list_cases(preview["run_id"], filters, limit=5000)
        payload = {"token": token, "current": [{"id": case["id"], "status": case["status"], "attemptStatus": case["attempt_status"]} for case in current]}

        def operation(connection: sqlite3.Connection) -> dict:
            if preview["action"] == "ignore":
                return self._ignore(connection, preview["run_id"], current, token)
            return self._resubmit(connection, preview["run_id"], current, token, idempotency_key)

        result, replayed = self.idempotency.perform(
            idempotency_key, f"recovery_{preview['action']}", payload, operation
        )
        with transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE recovery_previews SET consumed_at = COALESCE(consumed_at, CURRENT_TIMESTAMP) WHERE token = ?",
                (token,),
            )
        for batch_id in result.get("batchIds", []):
            self.queue.enqueue(f"submit:{batch_id}", "submit_batch", {"batchId": batch_id})
        for ready in result.get("readyAlgorithmCalls", []):
            self.queue.enqueue(
                f"postprocess:{ready['callId']}:{ready['generation']}", "run_postprocess",
                {"algorithmCallId": ready["callId"], "generation": ready["generation"]},
            )
        return result, replayed

    def _ignore(self, connection, run_id: str, cases: list[dict], token: str) -> dict:
        call_ids = set()
        for case in cases:
            connection.execute(
                "UPDATE testcases SET status = 'ignored', ignored_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (case["id"],),
            )
            if case["attempt_id"]:
                connection.execute(
                    "UPDATE execution_attempts SET status = 'ignored', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (case["attempt_id"],),
                )
            call_id = connection.execute(
                "SELECT algorithm_call_id FROM testcases WHERE id = ?", (case["id"],)
            ).fetchone()[0]
            call_ids.add(call_id)
        ready = []
        for call_id in call_ids:
            statuses = [row[0] for row in connection.execute(
                "SELECT status FROM testcases WHERE algorithm_call_id = ?", (call_id,)
            )]
            if algorithm_group_ready(statuses):
                generation = connection.execute(
                    "SELECT COALESCE(MAX(attempt_number), 1) FROM execution_attempts WHERE testcase_id IN (SELECT id FROM testcases WHERE algorithm_call_id = ?)",
                    (call_id,),
                ).fetchone()[0]
                ready.append({"callId": call_id, "generation": generation})
        connection.execute(
            "INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id, payload_json) VALUES (?, 'testcases_ignored', 'recovery_preview', ?, ?)",
            (run_id, token, json.dumps({"testcaseIds": [case["id"] for case in cases]})),
        )
        return {"action": "ignore", "ignoredCount": len(cases), "readyAlgorithmCalls": ready}

    def _resubmit(
        self, connection, run_id: str, cases: list[dict], token: str, operation_key: str
    ) -> dict:
        grouped: dict[tuple, list[dict]] = {}
        for case in cases:
            group = connection.execute(
                """
                SELECT rc.id AS run_corner_id, t.stage_execution_id, t.algorithm_call_id
                FROM testcases t JOIN stage_executions se ON se.id = t.stage_execution_id
                JOIN run_corners rc ON rc.id = se.run_corner_id WHERE t.id = ?
                """,
                (case["id"],),
            ).fetchone()
            key = (group["run_corner_id"], group["stage_execution_id"], group["algorithm_call_id"])
            grouped.setdefault(key, []).append(case)
        batch_ids = []
        for group_number, (group, selected) in enumerate(grouped.items(), 1):
            run_corner_id, stage_id, call_id = group
            batch_id = f"retry_{uuid.uuid5(uuid.NAMESPACE_URL, f'{operation_key}:{group_number}').hex}"
            job_name = f"flow_{batch_id[-20:]}"
            stage_path = Path(connection.execute(
                "SELECT directory_path FROM stage_executions WHERE id = ?", (stage_id,)
            ).fetchone()[0])
            items = []
            rows_to_insert = []
            for index, case in enumerate(selected, 1):
                if case["attempt_id"] and case["attempt_status"] == "copyback_waiting":
                    connection.execute(
                        """
                        UPDATE execution_attempts
                        SET status = 'abandoned', abandoned_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND status = 'copyback_waiting'
                        """,
                        (case["attempt_id"],),
                    )
                attempt_number = connection.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM execution_attempts WHERE testcase_id = ?",
                    (case["id"],),
                ).fetchone()[0]
                attempt_identity = f"{operation_key}:{case['id']}"
                attempt_id = f"attempt_{uuid.uuid5(uuid.NAMESPACE_URL, attempt_identity).hex}"
                status_path = Path(case["case_path"]) / "status.json"
                write_execution_status(
                    status_path, state="submit", batchId=batch_id, testcaseId=case["id"],
                    attemptId=attempt_id, arrayIndex=index,
                )
                items.append(BatchItem(
                    index, case["id"], attempt_id, case["case_path"], case["sp_path"], str(status_path)
                ))
                rows_to_insert.append((attempt_id, case["id"], attempt_number, index))
            manifest = BatchManifest.create(
                batch_id, job_name, self.simulator_command, items, self.expected_files
            )
            manifest_path = stage_path / f"{batch_id}.json"
            if not manifest_path.exists():
                manifest.write_once(manifest_path)
            connection.execute(
                """
                INSERT OR IGNORE INTO batches(
                    id, run_id, run_corner_id, stage_execution_id, algorithm_call_id,
                    unique_job_name, submission_type, status, manifest_path, scheduler_settings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (batch_id, run_id, run_corner_id, stage_id, call_id, job_name,
                 "job" if len(items) == 1 else "array", str(manifest_path), json.dumps(self.scheduler_settings)),
            )
            for attempt_id, testcase_id, attempt_number, index in rows_to_insert:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO execution_attempts(
                        id, testcase_id, attempt_number, reason, status, batch_id, array_index
                    ) VALUES (?, ?, ?, 'manual_resubmit', 'ready', ?, ?)
                    """,
                    (attempt_id, testcase_id, attempt_number, batch_id, index),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO batch_items(batch_id, attempt_id, array_index) VALUES (?, ?, ?)",
                    (batch_id, attempt_id, index),
                )
                connection.execute(
                    "UPDATE testcases SET status = 'preparing', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (testcase_id,),
                )
            active = connection.execute(
                "SELECT active FROM stage_executions WHERE id = ?", (stage_id,)
            ).fetchone()[0]
            if active:
                generating_call_number = connection.execute(
                    "SELECT call_number FROM algorithm_calls WHERE id = ?", (call_id,)
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE algorithm_calls SET superseded_at = CURRENT_TIMESTAMP
                    WHERE stage_execution_id = ? AND call_number > ? AND superseded_at IS NULL
                    """,
                    (stage_id, generating_call_number),
                )
                connection.execute(
                    "UPDATE stage_executions SET status = 'simulating', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stage_id,),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'preparing', final_result_json = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (run_corner_id,),
                )
                connection.execute(
                    "UPDATE runs SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (run_id,),
                )
            batch_ids.append(batch_id)
        connection.execute(
            "INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id, payload_json) VALUES (?, 'testcases_resubmitted', 'recovery_preview', ?, ?)",
            (run_id, token, json.dumps({"testcaseIds": [case["id"] for case in cases], "batchIds": batch_ids})),
        )
        return {"action": "resubmit", "resubmittedCount": len(cases), "batchIds": batch_ids}
