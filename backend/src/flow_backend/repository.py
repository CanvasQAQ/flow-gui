from pathlib import Path
import json

from .db import connect


class QueryRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def list_datasets(self) -> list[dict]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT d.id, d.name, d.source_path, d.scan_status, d.scanned_at,
                       COUNT(c.id) AS total,
                       COALESCE(SUM(CASE WHEN c.availability = 'available' THEN 1 ELSE 0 END), 0) AS available,
                       COALESCE(SUM(CASE WHEN c.availability = 'invalid' THEN 1 ELSE 0 END), 0) AS invalid,
                       COALESCE(SUM(CASE WHEN c.availability = 'missing' THEN 1 ELSE 0 END), 0) AS missing
                FROM datasets d
                LEFT JOIN corners c ON c.dataset_id = d.id
                GROUP BY d.id
                ORDER BY d.created_at DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "path": row["source_path"],
                "scanStatus": row["scan_status"],
                "scannedAt": row["scanned_at"],
                "total": row["total"],
                "available": row["available"],
                "invalid": row["invalid"],
                "missing": row["missing"],
            }
            for row in rows
        ]

    def list_runs(self) -> list[dict]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.name, r.status, r.algorithm_scheme, r.pvt_json,
                       r.created_at, r.updated_at, d.id AS dataset_id, d.name AS dataset_name,
                       COUNT(rc.id) AS corner_count,
                       COALESCE(SUM(CASE WHEN rc.status = 'done' THEN 1 ELSE 0 END), 0) AS completed_count,
                       COALESCE(SUM(CASE WHEN rc.status IN ('waiting_user', 'status_unknown', 'failed') THEN 1 ELSE 0 END), 0) AS attention_count
                FROM runs r
                JOIN datasets d ON d.id = r.dataset_id
                LEFT JOIN run_corners rc ON rc.run_id = r.id
                GROUP BY r.id
                ORDER BY r.created_at DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "algorithm": row["algorithm_scheme"],
                "pvt": json.loads(row["pvt_json"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "dataset": {"id": row["dataset_id"], "name": row["dataset_name"]},
                "summary": {
                    "total": row["corner_count"],
                    "completed": row["completed_count"],
                    "attention": row["attention_count"],
                },
            }
            for row in rows
        ]

    def latest_revision(self) -> int:
        with connect(self.database_path) as connection:
            return int(connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM workflow_events"
            ).fetchone()[0])

    def list_events(self, after_id: int = 0, limit: int = 200) -> list[dict]:
        safe_limit = max(1, min(limit, 1000))
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, run_corner_id, event_type, entity_type,
                       entity_id, payload_json, created_at
                FROM workflow_events WHERE id > ? ORDER BY id LIMIT ?
                """,
                (max(0, after_id), safe_limit),
            ).fetchall()
        return [
            {
                "id": row["id"], "runId": row["run_id"],
                "runCornerId": row["run_corner_id"], "type": row["event_type"],
                "entityType": row["entity_type"], "entityId": row["entity_id"],
                "payload": json.loads(row["payload_json"] or "{}"),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def runtime_status(self) -> dict:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_components ORDER BY name"
            ).fetchall()
        components = {
            row["name"]: {
                "state": row["state"],
                "detail": json.loads(row["detail_json"] or "{}"),
                "processed": row["processed_count"], "total": row["total_count"],
                "startedAt": row["started_at"], "lastHeartbeatAt": row["last_heartbeat_at"],
                "lastSuccessAt": row["last_success_at"], "nextDueAt": row["next_due_at"],
                "error": json.loads(row["error_json"]) if row["error_json"] else None,
                "updatedAt": row["updated_at"],
            }
            for row in rows
        }
        checker = components.get("status_checker", {
            "state": "idle", "detail": {"message": "No status check has run yet"},
        })
        return {
            "backend": {"state": "ready", "message": "Backend is responding"},
            "checker": checker,
            "workers": components,
        }

    def ui_snapshot(self) -> dict:
        runs = self.list_runs()
        for run in runs:
            run["datasetId"] = run["dataset"]["id"]
            run["datasetName"] = run["dataset"]["name"]
            run["corners"] = self.list_run_corners_detailed(run["id"])
            run["recoveryCases"] = self.list_recovery_cases(run["id"])
        return {
            "revision": self.latest_revision(),
            "datasets": self.list_datasets(),
            "runs": runs,
            "runtime": self.runtime_status(),
        }

    def list_dataset_corners(self, dataset_id: str) -> list[dict]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.corner_number, c.corner_key, c.availability, c.error_json,
                       c.last_checked_at, v.id AS input_version_id, v.version_number,
                       v.initial_code_json, v.reference_metrics_json
                FROM corners c
                LEFT JOIN corner_input_versions v ON v.id = c.current_input_version_id
                WHERE c.dataset_id = ? ORDER BY c.corner_number
                """,
                (dataset_id,),
            ).fetchall()
        return [
            {
                "id": row["id"], "number": row["corner_number"], "key": row["corner_key"],
                "availability": row["availability"],
                "error": json.loads(row["error_json"]) if row["error_json"] else None,
                "lastCheckedAt": row["last_checked_at"],
                "inputVersion": (
                    {"id": row["input_version_id"], "number": row["version_number"],
                     "initialCode": json.loads(row["initial_code_json"]),
                     "referenceMetrics": json.loads(row["reference_metrics_json"])}
                    if row["input_version_id"] else None
                ),
            }
            for row in rows
        ]

    def list_run_corners(self, run_id: str) -> list[dict]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT rc.id, c.corner_number, c.corner_key, rc.status, rc.input_version_id,
                       rc.active_stage_execution_id, rc.final_result_json, rc.updated_at,
                       c.current_input_version_id
                FROM run_corners rc JOIN corners c ON c.id = rc.corner_id
                WHERE rc.run_id = ? ORDER BY c.corner_number
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "id": row["id"], "number": row["corner_number"], "key": row["corner_key"],
                "status": row["status"], "inputVersionId": row["input_version_id"],
                "inputUpdateAvailable": row["input_version_id"] != row["current_input_version_id"],
                "activeStageExecutionId": row["active_stage_execution_id"],
                "finalResult": json.loads(row["final_result_json"]) if row["final_result_json"] else None,
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def list_run_corners_detailed(self, run_id: str) -> list[dict]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT rc.id, c.corner_number, c.corner_key, rc.status,
                       rc.input_version_id, rc.final_result_json, rc.updated_at,
                       c.current_input_version_id, v.version_number, v.initial_code_json,
                       se.id AS stage_id, se.stage_key, se.execution_number, se.status AS stage_status,
                       (SELECT COALESCE(MAX(ac.call_number), 0) FROM algorithm_calls ac
                        WHERE ac.stage_execution_id = se.id) AS call_number,
                       (SELECT COUNT(*) FROM testcases t WHERE t.stage_execution_id = se.id) AS testcase_total,
                       (SELECT COUNT(*) FROM testcases t WHERE t.stage_execution_id = se.id
                        AND t.status IN ('done', 'succeeded', 'ignored')) AS testcase_done,
                       (SELECT b.status FROM batches b WHERE b.stage_execution_id = se.id
                        ORDER BY b.created_at DESC LIMIT 1) AS batch_status,
                       (SELECT pa.status FROM postprocess_attempts pa JOIN algorithm_calls ac ON ac.id = pa.algorithm_call_id
                        WHERE ac.stage_execution_id = se.id ORDER BY pa.created_at DESC LIMIT 1) AS post_status
                FROM run_corners rc
                JOIN corners c ON c.id = rc.corner_id
                JOIN corner_input_versions v ON v.id = rc.input_version_id
                LEFT JOIN stage_executions se ON se.id = rc.active_stage_execution_id
                WHERE rc.run_id = ? ORDER BY c.corner_number
                """,
                (run_id,),
            ).fetchall()

        def step_states(row) -> list[dict]:
            corner_status = row["status"]
            stage_status = row["stage_status"]
            batch_status = row["batch_status"]
            post_status = row["post_status"]
            attention = corner_status in {"failed", "status_unknown", "waiting_user", "copyback_waiting"}
            deciding = stage_status in {"deciding", "preparing"}
            submitted = batch_status is not None
            sim_done = row["testcase_total"] > 0 and row["testcase_done"] >= row["testcase_total"]
            specs = [
                ("algorithm_decision", not deciding and row["call_number"] > 0, deciding, False),
                ("prepare_cases", submitted, row["call_number"] > 0 and not submitted, False),
                ("submit_batch", batch_status not in {None, "ready", "submitting"}, batch_status in {"ready", "submitting"}, False),
                ("simulate_group", sim_done, submitted and not sim_done and not attention, attention),
                ("recovery_gate", not attention and sim_done, False, attention),
                ("postprocess", post_status in {"complete", "completed", "succeeded"}, post_status in {"pending", "running"}, False),
            ]
            return [
                {"key": key, "state": "complete" if complete else "attention" if attention_now else "ongoing" if ongoing else "waiting"}
                for key, complete, ongoing, attention_now in specs
            ]

        items = []
        for row in rows:
            result = json.loads(row["final_result_json"]) if row["final_result_json"] else {}
            issue = None
            if row["status"] == "copyback_waiting":
                issue = "Simulation finished, but results could not be copied back. Retry copy or rerun."
            elif row["status"] in {"failed", "status_unknown", "waiting_user"}:
                issue = "Backend needs user attention before this Corner can continue."
            items.append({
                "id": row["id"], "number": row["corner_number"], "key": row["corner_key"],
                "status": row["status"], "state": row["status"],
                "stage": row["stage_key"] or "stage1", "round": row["call_number"] or 1,
                "testcaseTotal": row["testcase_total"], "testcaseDone": row["testcase_done"],
                "inputVersionId": row["input_version_id"], "inputVersion": f"v{row['version_number']}",
                "initialCode": json.loads(row["initial_code_json"] or "{}"),
                "inputUpdateAvailable": row["input_version_id"] != row["current_input_version_id"],
                "updateAvailable": row["input_version_id"] != row["current_input_version_id"],
                "finalResult": result or None,
                "loss": result.get("loss", result.get("totalLoss")),
                "updatedAt": row["updated_at"], "issue": issue,
                "workflowSteps": step_states(row),
            })
        return items

    def list_recovery_cases(self, run_id: str, limit: int = 5000) -> list[dict]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT t.id AS testcase_id, t.name, t.status, c.corner_number,
                       se.stage_key, ac.call_number, ea.id AS attempt_id,
                       ea.attempt_number, ea.status AS attempt_status, ea.lsf_job_id,
                       ea.array_index, ea.evidence_json, ea.copyback_error_json,
                       ea.last_checked_at, ea.execution_host
                FROM testcases t
                JOIN algorithm_calls ac ON ac.id = t.algorithm_call_id
                JOIN stage_executions se ON se.id = t.stage_execution_id
                JOIN run_corners rc ON rc.id = se.run_corner_id
                JOIN corners c ON c.id = rc.corner_id
                LEFT JOIN execution_attempts ea ON ea.testcase_id = t.id
                  AND ea.attempt_number = (
                    SELECT MAX(x.attempt_number) FROM execution_attempts x WHERE x.testcase_id = t.id
                  )
                WHERE rc.run_id = ? AND t.status IN (
                    'failed', 'status_unknown', 'copyback_waiting', 'waiting_user'
                )
                ORDER BY c.corner_number, se.created_at, ac.call_number, t.name LIMIT ?
                """,
                (run_id, max(1, min(limit, 5000))),
            ).fetchall()
        return [
            {
                "testcaseId": row["testcase_id"], "name": row["name"],
                "status": row["status"], "cornerNumber": row["corner_number"],
                "stageKey": row["stage_key"], "callNumber": row["call_number"],
                "attemptId": row["attempt_id"], "attempt": row["attempt_number"],
                "attemptStatus": row["attempt_status"], "lsfJobId": row["lsf_job_id"],
                "arrayIndex": row["array_index"], "host": row["execution_host"],
                "evidence": json.loads(row["evidence_json"] or "{}"),
                "copybackError": json.loads(row["copyback_error_json"]) if row["copyback_error_json"] else None,
                "updatedAt": row["last_checked_at"],
            }
            for row in rows
        ]
