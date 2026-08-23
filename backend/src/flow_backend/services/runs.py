from pathlib import Path
from typing import Any, Sequence
import json
import sqlite3
import uuid

from ..idempotency import IdempotencyStore
from ..work_queue import DurableWorkQueue


class RunServiceError(ValueError):
    pass


class RunService:
    def __init__(self, database_path: Path, workspace_dir: Path):
        self.database_path = database_path
        self.workspace_dir = workspace_dir
        self.idempotency = IdempotencyStore(database_path)
        self.queue = DurableWorkQueue(database_path)

    def create(
        self,
        idempotency_key: str,
        *,
        dataset_id: str,
        name: str,
        pvt: dict[str, Any],
        algorithm_scheme: str,
        algorithm_config: dict[str, Any],
        corner_numbers: Sequence[int],
    ) -> tuple[dict, bool]:
        payload = {
            "datasetId": dataset_id, "name": name, "pvt": pvt,
            "algorithmScheme": algorithm_scheme, "algorithmConfig": algorithm_config,
            "cornerNumbers": sorted(set(corner_numbers)),
        }

        def operation(connection: sqlite3.Connection) -> dict:
            numbers = payload["cornerNumbers"]
            if not numbers:
                raise RunServiceError("at least one Corner must be selected")
            placeholders = ",".join("?" for _ in numbers)
            rows = connection.execute(
                f"""
                SELECT id, corner_number, current_input_version_id FROM corners
                WHERE dataset_id = ? AND availability = 'available'
                  AND corner_number IN ({placeholders})
                """,
                [dataset_id, *numbers],
            ).fetchall()
            found = {row["corner_number"] for row in rows}
            missing = sorted(set(numbers) - found)
            if missing:
                raise RunServiceError(f"Corners are missing or unavailable: {missing}")
            run_id = f"run_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO runs(id, dataset_id, name, status, algorithm_scheme,
                    algorithm_config_json, pvt_json)
                VALUES (?, ?, ?, 'running', ?, ?, ?)
                """,
                (run_id, dataset_id, name, algorithm_scheme, json.dumps(algorithm_config), json.dumps(pvt)),
            )
            run_corner_ids = []
            for row in rows:
                run_corner_id = f"rc_{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO run_corners(id, run_id, corner_id, input_version_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (run_corner_id, run_id, row["id"], row["current_input_version_id"]),
                )
                run_corner_ids.append(run_corner_id)
            connection.execute(
                "INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id, payload_json) VALUES (?, 'run_created', 'run', ?, ?)",
                (run_id, run_id, json.dumps(payload)),
            )
            return {"runId": run_id, "cornerCount": len(run_corner_ids), "runCornerIds": run_corner_ids}

        result, replayed = self.idempotency.perform(idempotency_key, "create_run", payload, operation)
        for run_corner_id in result["runCornerIds"]:
            self.queue.enqueue(
                f"initialize:{run_corner_id}", "initialize_run_corner", {"runCornerId": run_corner_id}
            )
        return result, replayed

    def set_paused(self, idempotency_key: str, run_id: str, paused: bool) -> tuple[dict, bool]:
        payload = {"runId": run_id, "paused": paused}

        def operation(connection: sqlite3.Connection) -> dict:
            status = "paused" if paused else "running"
            changed = connection.execute(
                """
                UPDATE runs SET status = ?, paused_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (status, paused, run_id),
            ).rowcount
            if changed != 1:
                raise RunServiceError(f"unknown Run: {run_id}")
            connection.execute(
                "INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id) VALUES (?, ?, 'run', ?)",
                (run_id, "run_paused" if paused else "run_resumed", run_id),
            )
            return {"runId": run_id, "status": status}

        return self.idempotency.perform(idempotency_key, "set_run_paused", payload, operation)

    def add_corners(
        self, idempotency_key: str, run_id: str, corner_numbers: Sequence[int]
    ) -> tuple[dict, bool]:
        payload = {"runId": run_id, "cornerNumbers": sorted(set(corner_numbers))}

        def operation(connection: sqlite3.Connection) -> dict:
            run = connection.execute("SELECT dataset_id FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise RunServiceError(f"unknown Run: {run_id}")
            numbers = payload["cornerNumbers"]
            if not numbers:
                raise RunServiceError("at least one Corner must be selected")
            placeholders = ",".join("?" for _ in numbers)
            rows = connection.execute(
                f"""
                SELECT c.id, c.corner_number, c.availability, c.current_input_version_id,
                       rc.id AS existing_run_corner_id
                FROM corners c
                LEFT JOIN run_corners rc ON rc.corner_id = c.id AND rc.run_id = ?
                WHERE c.dataset_id = ? AND c.corner_number IN ({placeholders})
                """,
                [run_id, run["dataset_id"], *numbers],
            ).fetchall()
            by_number = {row["corner_number"]: row for row in rows}
            added_ids = []
            already_present = []
            unavailable = []
            not_found = []
            for number in numbers:
                row = by_number.get(number)
                if row is None:
                    not_found.append(number)
                elif row["existing_run_corner_id"]:
                    already_present.append(number)
                elif row["availability"] != "available" or not row["current_input_version_id"]:
                    unavailable.append(number)
                else:
                    run_corner_id = f"rc_{uuid.uuid4().hex}"
                    connection.execute(
                        "INSERT INTO run_corners(id, run_id, corner_id, input_version_id) VALUES (?, ?, ?, ?)",
                        (run_corner_id, run_id, row["id"], row["current_input_version_id"]),
                    )
                    added_ids.append(run_corner_id)
            result = {
                "runId": run_id, "addedCount": len(added_ids), "runCornerIds": added_ids,
                "alreadyPresent": already_present, "unavailable": unavailable, "notFound": not_found,
            }
            connection.execute(
                "INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id, payload_json) VALUES (?, 'corners_added', 'run', ?, ?)",
                (run_id, run_id, json.dumps(result)),
            )
            return result

        result, replayed = self.idempotency.perform(idempotency_key, "add_run_corners", payload, operation)
        for run_corner_id in result["runCornerIds"]:
            self.queue.enqueue(
                f"initialize:{run_corner_id}", "initialize_run_corner", {"runCornerId": run_corner_id}
            )
        return result, replayed
