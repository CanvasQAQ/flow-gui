from pathlib import Path
from typing import Sequence
import json
import sqlite3

from ..db import connect
from ..idempotency import IdempotencyStore
from ..work_queue import DurableWorkQueue


class RollbackError(ValueError):
    pass


class RollbackService:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.idempotency = IdempotencyStore(database_path)
        self.queue = DurableWorkQueue(database_path)

    def preview(self, run_id: str, corner_numbers: Sequence[int], target_stage: str) -> dict:
        results = []
        with connect(self.database_path) as connection:
            for number in sorted(set(corner_numbers)):
                run_corner = connection.execute(
                    """
                    SELECT rc.id FROM run_corners rc JOIN corners c ON c.id = rc.corner_id
                    WHERE rc.run_id = ? AND c.corner_number = ?
                    """,
                    (run_id, number),
                ).fetchone()
                if run_corner is None:
                    results.append({"cornerNumber": number, "eligible": False, "reason": "corner_not_in_run"})
                    continue
                target = connection.execute(
                    """
                    SELECT * FROM stage_executions WHERE run_corner_id = ? AND stage_key = ?
                      AND superseded_at IS NULL ORDER BY execution_number DESC LIMIT 1
                    """,
                    (run_corner["id"], target_stage),
                ).fetchone()
                if target is None:
                    results.append({"cornerNumber": number, "eligible": False, "reason": "target_stage_not_reached"})
                    continue
                decision = connection.execute(
                    """
                    SELECT * FROM algorithm_calls WHERE stage_execution_id = ?
                      AND applied_at IS NOT NULL AND superseded_at IS NULL
                    ORDER BY call_number DESC LIMIT 1
                    """,
                    (target["id"],),
                ).fetchone()
                if decision is None or decision["decision_kind"] == "add_testcases":
                    results.append({"cornerNumber": number, "eligible": False, "reason": "target_decision_not_complete"})
                    continue
                active_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM execution_attempts ea
                    JOIN testcases t ON t.id = ea.testcase_id
                    JOIN stage_executions se ON se.id = t.stage_execution_id
                    WHERE se.run_corner_id = ? AND se.created_at > ? AND se.superseded_at IS NULL
                      AND ea.attempt_number = (SELECT MAX(x.attempt_number) FROM execution_attempts x WHERE x.testcase_id = t.id)
                      AND ea.status IN ('submitting', 'pending', 'running', 'status_unknown')
                    """,
                    (run_corner["id"], target["created_at"]),
                ).fetchone()[0]
                if active_count:
                    results.append({
                        "cornerNumber": number, "eligible": False,
                        "reason": "downstream_jobs_not_terminal", "activeAttemptCount": active_count,
                    })
                    continue
                results.append({
                    "cornerNumber": number, "eligible": True, "reason": "ready",
                    "runCornerId": run_corner["id"], "targetStageExecutionId": target["id"],
                    "targetDecisionId": decision["id"], "nextCallNumber": decision["call_number"] + 1,
                })
        return {
            "runId": run_id, "targetStage": target_stage,
            "eligibleCount": sum(item["eligible"] for item in results), "items": results,
        }

    def execute(
        self, idempotency_key: str, run_id: str, corner_numbers: Sequence[int], target_stage: str
    ) -> tuple[dict, bool]:
        preview = self.preview(run_id, corner_numbers, target_stage)
        payload = {"runId": run_id, "cornerNumbers": sorted(set(corner_numbers)), "targetStage": target_stage}

        def operation(connection: sqlite3.Connection) -> dict:
            processed = []
            skipped = []
            for item in preview["items"]:
                if not item["eligible"]:
                    skipped.append(item)
                    continue
                run_corner_id = item["runCornerId"]
                target_stage_id = item["targetStageExecutionId"]
                target = connection.execute(
                    "SELECT created_at FROM stage_executions WHERE id = ?", (target_stage_id,)
                ).fetchone()
                connection.execute(
                    """
                    UPDATE algorithm_calls SET superseded_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND superseded_at IS NULL
                    """,
                    (item["targetDecisionId"],),
                )
                connection.execute(
                    """
                    UPDATE algorithm_calls SET superseded_at = CURRENT_TIMESTAMP
                    WHERE stage_execution_id IN (
                        SELECT id FROM stage_executions WHERE run_corner_id = ? AND created_at > ?
                    ) AND superseded_at IS NULL
                    """,
                    (run_corner_id, target["created_at"]),
                )
                connection.execute(
                    """
                    UPDATE stage_executions SET active = 0, status = 'superseded',
                        superseded_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE run_corner_id = ? AND created_at > ? AND superseded_at IS NULL
                    """,
                    (run_corner_id, target["created_at"]),
                )
                connection.execute(
                    """
                    UPDATE stage_executions SET active = 1, status = 'deciding',
                        superseded_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (target_stage_id,),
                )
                connection.execute(
                    """
                    UPDATE run_corners SET active_stage_execution_id = ?, status = 'preparing',
                        final_result_json = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (target_stage_id, run_corner_id),
                )
                connection.execute(
                    "INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id, payload_json) VALUES (?, ?, 'rollback_applied', 'stage_execution', ?, ?)",
                    (run_id, run_corner_id, target_stage_id, json.dumps({"targetDecisionId": item["targetDecisionId"]})),
                )
                processed.append(item)
            if processed:
                connection.execute(
                    "UPDATE runs SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (run_id,),
                )
            return {"processed": processed, "skipped": skipped}

        result, replayed = self.idempotency.perform(
            idempotency_key, "rollback", payload, operation
        )
        for item in result["processed"]:
            self.queue.enqueue(
                f"decide:{item['targetStageExecutionId']}:{item['nextCallNumber']}",
                "decide_stage",
                {"stageExecutionId": item["targetStageExecutionId"], "callNumber": item["nextCallNumber"]},
            )
        return result, replayed

