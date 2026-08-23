from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence
import json
import re
import uuid

from .batch import BatchItem, BatchManifest, read_execution_status, write_execution_status
from .adapters.lsf import LsfSubmissionUnknown
from .db import connect, transaction
from .ports import PostprocessAdapter, SchedulerAdapter, SchedulerSnapshotReader, SimulatorAdapter
from .work_queue import DurableWorkQueue, PermanentWorkError, RetryWork
from .workflow import AlgorithmDecision, AttemptEvidence, algorithm_group_ready, classify_attempt


class CoordinatorError(RuntimeError):
    pass


_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_ID_NAMESPACE = uuid.UUID("ada7a5b6-0a5d-4b15-9d6b-cbcd1a4e2362")


def _id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{uuid.uuid5(_ID_NAMESPACE, ':'.join(map(str, parts))).hex}"


class WorkflowCoordinator:
    """Durable single-step workflow handlers.

    Each handler is safe to call again. External integrations remain adapters;
    the coordinator only persists intent before exposing work to LSF.
    """

    def __init__(
        self,
        database_path: Path,
        workspace_dir: Path,
        algorithms,
        simulator: SimulatorAdapter,
        scheduler: SchedulerAdapter,
        postprocess: PostprocessAdapter,
        simulator_command: Sequence[str],
        expected_files: Sequence[str] = (),
        scheduler_settings: Mapping[str, Any] | None = None,
        snapshot_reader: SchedulerSnapshotReader | None = None,
    ):
        self.database_path = database_path
        self.workspace_dir = workspace_dir
        self.algorithms = algorithms
        self.simulator = simulator
        self.scheduler = scheduler
        self.postprocess = postprocess
        self.simulator_command = tuple(simulator_command)
        self.expected_files = tuple(expected_files)
        self.scheduler_settings = dict(scheduler_settings or {})
        self.snapshot_reader = snapshot_reader
        self.queue = DurableWorkQueue(database_path)

    @property
    def handlers(self) -> dict[str, Any]:
        return {
            "initialize_run_corner": self.initialize_run_corner,
            "decide_stage": self.decide_stage,
            "submit_batch": self.submit_batch,
            "run_postprocess": self.run_postprocess,
        }

    def initialize_run_corner(self, payload: dict[str, Any]) -> None:
        run_corner_id = payload["runCornerId"]
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT rc.*, r.id AS run_id, r.status AS run_status, r.algorithm_scheme,
                       c.corner_number
                FROM run_corners rc JOIN runs r ON r.id = rc.run_id
                JOIN corners c ON c.id = rc.corner_id WHERE rc.id = ?
                """,
                (run_corner_id,),
            ).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown Run Corner: {run_corner_id}")
        if row["run_status"] == "paused":
            raise RetryWork("Run is paused", 10)
        adapter = self.algorithms.get(row["algorithm_scheme"])
        stages = adapter.stage_definitions()
        stage_key = payload.get("stageKey") or stages[0]["key"]
        if not _SAFE_NAME.fullmatch(stage_key):
            raise CoordinatorError("Stage keys must be filesystem-safe")

        with transaction(self.database_path) as connection:
            active = connection.execute(
                "SELECT id FROM stage_executions WHERE run_corner_id = ? AND active = 1",
                (run_corner_id,),
            ).fetchone()
            if active is not None:
                return
            execution_number = connection.execute(
                "SELECT COALESCE(MAX(execution_number), 0) + 1 FROM stage_executions WHERE run_corner_id = ? AND stage_key = ?",
                (run_corner_id, stage_key),
            ).fetchone()[0]
            stage_id = _id("stage", run_corner_id, stage_key, execution_number)
            directory = (
                self.workspace_dir / row["run_id"] / f"corner_{row['corner_number']:04d}"
                / f"{stage_key}__{execution_number:03d}"
            )
            directory.mkdir(parents=True, exist_ok=True)
            connection.execute(
                """
                INSERT OR IGNORE INTO stage_executions(
                    id, run_corner_id, stage_key, execution_number, status, directory_path
                ) VALUES (?, ?, ?, ?, 'deciding', ?)
                """,
                (stage_id, run_corner_id, stage_key, execution_number, str(directory)),
            )
            connection.execute(
                "UPDATE run_corners SET active_stage_execution_id = ?, status = 'preparing', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (stage_id, run_corner_id),
            )
        self.queue.enqueue(
            f"decide:{stage_id}:1", "decide_stage", {"stageExecutionId": stage_id, "callNumber": 1}
        )

    def decide_stage(self, payload: dict[str, Any]) -> None:
        stage_id = payload["stageExecutionId"]
        call_number = int(payload["callNumber"])
        context = self._algorithm_context(stage_id, call_number)
        if context["runStatus"] == "paused":
            raise RetryWork("Run is paused", 10)
        existing = context.get("existingCall")
        if existing and existing["applied_at"]:
            return
        try:
            adapter = self.algorithms.get(context["algorithmScheme"])
            decision: AlgorithmDecision = adapter.decide(context["stageKey"], context["input"])
            decision.validate([stage["key"] for stage in adapter.stage_definitions()])
            call_id = _id("call", stage_id, call_number)
            if decision.kind == "add_testcases":
                self._apply_testcases(context, call_id, call_number, decision)
            else:
                self._apply_terminal_decision(context, call_id, call_number, decision)
        except Exception as exc:
            with transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE stage_executions SET status = 'blocked', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stage_id,),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'waiting_user', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (context["runCornerId"],),
                )
            raise PermanentWorkError(f"algorithm or Testcase preparation failed: {exc}") from exc

    def _algorithm_context(self, stage_id: str, call_number: int) -> dict[str, Any]:
        with connect(self.database_path) as connection:
            stage = connection.execute(
                """
                SELECT se.*, rc.id AS run_corner_id, rc.status AS corner_status,
                       r.id AS run_id, r.status AS run_status, r.algorithm_scheme,
                       r.algorithm_config_json, r.pvt_json, c.corner_number,
                       v.sp_snapshot_path, v.initial_code_json
                FROM stage_executions se
                JOIN run_corners rc ON rc.id = se.run_corner_id
                JOIN runs r ON r.id = rc.run_id JOIN corners c ON c.id = rc.corner_id
                JOIN corner_input_versions v ON v.id = rc.input_version_id
                WHERE se.id = ? AND se.active = 1
                """,
                (stage_id,),
            ).fetchone()
            if stage is None:
                raise CoordinatorError(f"active Stage execution not found: {stage_id}")
            testcases = connection.execute(
                "SELECT name, parameters_json, result_json, status FROM testcases WHERE stage_execution_id = ? ORDER BY created_at, id",
                (stage_id,),
            ).fetchall()
            previous_results = connection.execute(
                """
                SELECT stage_key, result_json FROM stage_executions
                WHERE run_corner_id = ? AND status = 'completed' AND result_json IS NOT NULL
                ORDER BY created_at
                """,
                (stage["run_corner_id"],),
            ).fetchall()
            existing_call = connection.execute(
                "SELECT * FROM algorithm_calls WHERE stage_execution_id = ? AND call_number = ?",
                (stage_id, call_number),
            ).fetchone()
        algorithm_input = {
            "run": {"id": stage["run_id"], "pvt": json.loads(stage["pvt_json"])},
            "corner": {"number": stage["corner_number"], "initialCode": json.loads(stage["initial_code_json"])},
            "stage": {"key": stage["stage_key"], "executionNumber": stage["execution_number"]},
            "verifiedTestcases": [
                {"name": row["name"], "parameters": json.loads(row["parameters_json"]),
                 "result": json.loads(row["result_json"]) if row["result_json"] else None,
                 "status": row["status"]}
                for row in testcases
            ],
            "previousStageResults": [
                {"stageKey": row["stage_key"], "result": json.loads(row["result_json"])}
                for row in previous_results
            ],
            "algorithmConfig": json.loads(stage["algorithm_config_json"]),
        }
        return {
            "stageId": stage_id, "stageKey": stage["stage_key"], "stageDirectory": stage["directory_path"],
            "runId": stage["run_id"], "runStatus": stage["run_status"],
            "runCornerId": stage["run_corner_id"], "algorithmScheme": stage["algorithm_scheme"],
            "sourceSp": stage["sp_snapshot_path"], "pvt": json.loads(stage["pvt_json"]),
            "input": algorithm_input, "existingCall": existing_call,
        }

    def _apply_testcases(
        self, context: dict, call_id: str, call_number: int, decision: AlgorithmDecision
    ) -> None:
        stage_directory = Path(context["stageDirectory"])
        batch_id = _id("batch", call_id)
        job_name = f"flow_{batch_id[-20:]}"
        items = []
        prepared = []
        for index, proposal in enumerate(decision.testcases, 1):
            if not _SAFE_NAME.fullmatch(proposal.name):
                raise CoordinatorError(f"unsafe Testcase name: {proposal.name!r}")
            testcase_id = _id("tc", call_id, proposal.name)
            attempt_id = _id("attempt", testcase_id, 1)
            case_path = stage_directory / proposal.name
            sp_path = case_path / "testcase.sp"
            status_path = case_path / "status.json"
            self.simulator.render_input(
                Path(context["sourceSp"]), sp_path, context["pvt"], dict(proposal.parameters)
            )
            write_execution_status(
                status_path, state="submit", batchId=batch_id, testcaseId=testcase_id,
                attemptId=attempt_id, arrayIndex=index,
            )
            items.append(BatchItem(index, testcase_id, attempt_id, str(case_path), str(sp_path), str(status_path)))
            prepared.append((proposal, testcase_id, attempt_id, case_path, sp_path, index))
        manifest = BatchManifest.create(batch_id, job_name, self.simulator_command, items, self.expected_files)
        manifest_path = stage_directory / f"{batch_id}.json"
        if manifest_path.exists():
            previous = BatchManifest.read(manifest_path)
            if previous.batch_id != manifest.batch_id or previous.items != manifest.items:
                raise CoordinatorError("existing immutable Batch manifest does not match retry")
        else:
            manifest.write_once(manifest_path)

        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO algorithm_calls(
                    id, stage_execution_id, call_number, algorithm_name, input_json,
                    output_json, decision_kind, reason, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (call_id, context["stageId"], call_number, context["algorithmScheme"],
                 json.dumps(context["input"]), json.dumps(asdict(decision)), decision.kind, decision.reason),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO batches(
                    id, run_id, run_corner_id, stage_execution_id, algorithm_call_id,
                    unique_job_name, submission_type, status, manifest_path, scheduler_settings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (batch_id, context["runId"], context["runCornerId"], context["stageId"], call_id,
                 job_name, "job" if len(items) == 1 else "array", str(manifest_path),
                 json.dumps(self.scheduler_settings)),
            )
            for proposal, testcase_id, attempt_id, case_path, sp_path, index in prepared:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO testcases(
                        id, algorithm_call_id, stage_execution_id, name, parameters_json,
                        case_path, sp_path, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'preparing')
                    """,
                    (testcase_id, call_id, context["stageId"], proposal.name,
                     json.dumps(dict(proposal.parameters)), str(case_path), str(sp_path)),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO execution_attempts(
                        id, testcase_id, attempt_number, reason, status, batch_id, array_index
                    ) VALUES (?, ?, 1, 'algorithm_proposal', 'ready', ?, ?)
                    """,
                    (attempt_id, testcase_id, batch_id, index),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO batch_items(batch_id, attempt_id, array_index) VALUES (?, ?, ?)",
                    (batch_id, attempt_id, index),
                )
            connection.execute(
                "UPDATE stage_executions SET status = 'simulating', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (context["stageId"],),
            )
        self.queue.enqueue(f"submit:{batch_id}", "submit_batch", {"batchId": batch_id})

    def _apply_terminal_decision(
        self, context: dict, call_id: str, call_number: int, decision: AlgorithmDecision
    ) -> None:
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO algorithm_calls(
                    id, stage_execution_id, call_number, algorithm_name, input_json,
                    output_json, decision_kind, reason, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (call_id, context["stageId"], call_number, context["algorithmScheme"],
                 json.dumps(context["input"]), json.dumps(asdict(decision)), decision.kind, decision.reason),
            )
            if decision.kind == "advance_stage":
                connection.execute(
                    "UPDATE stage_executions SET status = 'completed', active = 0, result_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(decision.result), context["stageId"]),
                )
                connection.execute(
                    "UPDATE run_corners SET active_stage_execution_id = NULL, status = 'preparing', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (context["runCornerId"],),
                )
            elif decision.kind == "complete":
                connection.execute(
                    "UPDATE stage_executions SET status = 'completed', result_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(decision.result), context["stageId"]),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'done', final_result_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(decision.result), context["runCornerId"]),
                )
            else:
                connection.execute(
                    "UPDATE stage_executions SET status = 'blocked', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (context["stageId"],),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'waiting_user', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (context["runCornerId"],),
                )
        if decision.kind == "advance_stage":
            key = f"initialize:{context['runCornerId']}:{decision.next_stage}"
            self.queue.enqueue(key, "initialize_run_corner", {
                "runCornerId": context["runCornerId"], "stageKey": decision.next_stage
            })
        elif decision.kind == "complete":
            self._complete_run_if_ready(context["runId"])

    def submit_batch(self, payload: dict[str, Any]) -> None:
        batch_id = payload["batchId"]
        with connect(self.database_path) as connection:
            batch = connection.execute(
                """
                SELECT b.*, r.status AS run_status FROM batches b
                JOIN runs r ON r.id = b.run_id WHERE b.id = ?
                """,
                (batch_id,),
            ).fetchone()
        if batch is None:
            raise CoordinatorError(f"unknown Batch: {batch_id}")
        if batch["status"] in {"submitted", "finished"}:
            return
        if batch["run_status"] == "paused":
            raise RetryWork("Run is paused", 10)
        if batch["status"] == "submitting":
            with transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE batches SET status = 'status_unknown', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (batch_id,),
                )
            return
        if batch["status"] != "ready":
            # In particular, status_unknown is never safe to submit again. It
            # must first be reconciled by unique Job name or handled manually.
            return
        with transaction(self.database_path) as connection:
            changed = connection.execute(
                """
                UPDATE batches SET status = 'submitting',
                       submission_started_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'ready'
                """,
                (batch_id,),
            ).rowcount
        if changed != 1:
            return
        try:
            job_id = self.scheduler.submit(
                Path(batch["manifest_path"]), batch["unique_job_name"], json.loads(batch["scheduler_settings_json"])
            )
        except LsfSubmissionUnknown as exc:
            with transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE batches SET status = 'status_unknown', submission_error_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps({"type": type(exc).__name__, "message": str(exc)}), batch_id),
                )
                connection.execute(
                    "UPDATE execution_attempts SET status = 'status_unknown', updated_at = CURRENT_TIMESTAMP WHERE batch_id = ?",
                    (batch_id,),
                )
                connection.execute(
                    "UPDATE testcases SET status = 'status_unknown', updated_at = CURRENT_TIMESTAMP WHERE id IN (SELECT testcase_id FROM execution_attempts WHERE batch_id = ?)",
                    (batch_id,),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'waiting_user', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (batch["run_corner_id"],),
                )
            raise PermanentWorkError(f"LSF submission outcome is unknown: {exc}") from exc
        except Exception as exc:
            with transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE batches SET status = 'failed', submission_error_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps({"type": type(exc).__name__, "message": str(exc)}), batch_id),
                )
                connection.execute(
                    "UPDATE execution_attempts SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE batch_id = ?",
                    (batch_id,),
                )
                connection.execute(
                    "UPDATE testcases SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id IN (SELECT testcase_id FROM execution_attempts WHERE batch_id = ?)",
                    (batch_id,),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'waiting_user', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (batch["run_corner_id"],),
                )
            raise PermanentWorkError(f"LSF submission failed: {exc}") from exc
        with transaction(self.database_path) as connection:
            connection.execute(
                "UPDATE batches SET status = 'submitted', lsf_job_id = ?, submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id, batch_id),
            )
            connection.execute(
                """
                UPDATE execution_attempts SET status = 'pending', lsf_job_id = ?,
                       next_check_at = datetime('now', '+10 minutes'),
                       updated_at = CURRENT_TIMESTAMP WHERE batch_id = ?
                """,
                (job_id, batch_id),
            )
            connection.execute(
                "UPDATE testcases SET status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id IN (SELECT testcase_id FROM execution_attempts WHERE batch_id = ?)",
                (batch_id,),
            )
            connection.execute(
                "UPDATE run_corners SET status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (batch["run_corner_id"],),
            )

    def reconcile(self) -> dict[str, int]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT ea.id AS attempt_id, ea.testcase_id, ea.status AS attempt_status,
                       ea.array_index, b.id AS batch_id, b.unique_job_name, b.status AS batch_status,
                       t.algorithm_call_id, t.stage_execution_id, t.case_path,
                       se.run_corner_id
                FROM execution_attempts ea JOIN batches b ON b.id = ea.batch_id
                JOIN testcases t ON t.id = ea.testcase_id
                JOIN stage_executions se ON se.id = t.stage_execution_id
                WHERE ea.status IN ('pending', 'running', 'status_unknown', 'copyback_waiting')
                """
            ).fetchall()
        observations: dict[tuple[str, int], dict] = {}
        if self.snapshot_reader and rows:
            names = sorted({row["unique_job_name"] for row in rows})
            for item in self.snapshot_reader.read_jobs(names):
                observations[(item["jobName"], int(item.get("arrayIndex") or 1))] = item
        changed = 0
        affected_calls = set()
        for row in rows:
            status_path = Path(row["case_path"]) / "status.json"
            status = None
            matches = False
            outputs_complete = False
            if status_path.is_file():
                try:
                    status = read_execution_status(status_path, row["attempt_id"])
                    matches = True
                    outputs_complete = not status.get("missingExpectedFiles")
                except Exception:
                    pass
            observation = observations.get((row["unique_job_name"], row["array_index"]))
            state = classify_attempt(AttemptEvidence(
                status.get("state") if status else None, matches,
                observation.get("status") if observation else None, outputs_complete,
            ))
            if state != row["attempt_status"] or status is not None:
                with transaction(self.database_path) as connection:
                    connection.execute(
                        """
                        UPDATE execution_attempts SET status = ?, evidence_json = ?,
                               scratch_path = COALESCE(?, scratch_path),
                               return_path = COALESCE(?, return_path),
                               execution_host = COALESCE(?, execution_host),
                               copyback_error_json = ?, last_checked_at = CURRENT_TIMESTAMP,
                               next_check_at = datetime('now', '+10 minutes'),
                               updated_at = CURRENT_TIMESTAMP WHERE id = ?
                        """,
                        (
                            state, json.dumps({"status": status, "snapshot": observation}),
                            status.get("scratchPath") if status else None,
                            status.get("returnPath") if status else None,
                            status.get("hostname") if status else None,
                            json.dumps(status.get("copybackError")) if status and status.get("copybackError") else None,
                            row["attempt_id"],
                        ),
                    )
                    connection.execute(
                        "UPDATE testcases SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (state, row["testcase_id"]),
                    )
                    corner_state = {
                        "pending": "queued", "running": "running",
                        "copyback_waiting": "copyback_waiting", "status_unknown": "status_unknown",
                        "failed": "waiting_user",
                    }.get(state)
                    if corner_state:
                        connection.execute(
                            "UPDATE run_corners SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (corner_state, row["run_corner_id"]),
                        )
                if state != row["attempt_status"]:
                    changed += 1
            affected_calls.add(row["algorithm_call_id"])
        queued = 0
        for call_id in affected_calls:
            with connect(self.database_path) as connection:
                statuses = [row[0] for row in connection.execute(
                    "SELECT status FROM testcases WHERE algorithm_call_id = ?", (call_id,)
                )]
                generation = connection.execute(
                    "SELECT COALESCE(MAX(attempt_number), 1) FROM execution_attempts WHERE testcase_id IN (SELECT id FROM testcases WHERE algorithm_call_id = ?)",
                    (call_id,),
                ).fetchone()[0]
            if algorithm_group_ready(statuses):
                self.queue.enqueue(
                    f"postprocess:{call_id}:{generation}", "run_postprocess",
                    {"algorithmCallId": call_id, "generation": generation},
                )
                with transaction(self.database_path) as connection:
                    connection.execute(
                        """
                        UPDATE run_corners SET status = 'postprocess', updated_at = CURRENT_TIMESTAMP
                        WHERE active_stage_execution_id = (
                            SELECT stage_execution_id FROM algorithm_calls WHERE id = ?
                        )
                        """,
                        (call_id,),
                    )
                queued += 1
        return {"changedAttempts": changed, "postprocessQueued": queued}

    def handle_attempt_observation(self, attempt_id: str, _document: dict | None = None) -> None:
        """Advance one Algorithm-call group after the status monitor updates an Attempt."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT ea.status, t.algorithm_call_id, se.run_corner_id
                FROM execution_attempts ea
                JOIN testcases t ON t.id = ea.testcase_id
                JOIN stage_executions se ON se.id = t.stage_execution_id
                WHERE ea.id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                return
            statuses = [item[0] for item in connection.execute(
                "SELECT status FROM testcases WHERE algorithm_call_id = ?",
                (row["algorithm_call_id"],),
            )]
            generation = connection.execute(
                """
                SELECT COALESCE(MAX(ea.attempt_number), 1)
                FROM execution_attempts ea JOIN testcases t ON t.id = ea.testcase_id
                WHERE t.algorithm_call_id = ?
                """,
                (row["algorithm_call_id"],),
            ).fetchone()[0]
        ready = algorithm_group_ready(statuses)
        corner_status = "postprocess" if ready else {
            "pending": "queued", "running": "running",
            "copyback_waiting": "copyback_waiting",
            "status_unknown": "status_unknown", "failed": "waiting_user",
        }.get(row["status"])
        if corner_status:
            with transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE run_corners SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (corner_status, row["run_corner_id"]),
                )
        if ready:
            call_id = row["algorithm_call_id"]
            self.queue.enqueue(
                f"postprocess:{call_id}:{generation}", "run_postprocess",
                {"algorithmCallId": call_id, "generation": generation},
            )

    def recover_interrupted(self) -> dict[str, int]:
        recovered_work = self.queue.recover_abandoned()
        with transaction(self.database_path) as connection:
            interrupted_batches = connection.execute(
                "UPDATE batches SET status = 'status_unknown', updated_at = CURRENT_TIMESTAMP WHERE status = 'submitting'"
            ).rowcount
            interrupted_attempts = connection.execute(
                """
                UPDATE execution_attempts SET status = 'status_unknown', updated_at = CURRENT_TIMESTAMP
                WHERE batch_id IN (SELECT id FROM batches WHERE status = 'status_unknown')
                  AND status IN ('ready', 'submitting')
                """
            ).rowcount
        return {
            "recoveredWorkItems": recovered_work,
            "unknownBatches": interrupted_batches,
            "unknownAttempts": interrupted_attempts,
        }

    def run_postprocess(self, payload: dict[str, Any]) -> None:
        call_id = payload["algorithmCallId"]
        generation = int(payload.get("generation", 1))
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT ac.*, se.directory_path, se.id AS stage_id, se.run_corner_id, se.active,
                       r.status AS run_status
                FROM algorithm_calls ac JOIN stage_executions se ON se.id = ac.stage_execution_id
                JOIN run_corners rc ON rc.id = se.run_corner_id JOIN runs r ON r.id = rc.run_id
                WHERE ac.id = ?
                """,
                (call_id,),
            ).fetchone()
            existing = connection.execute(
                "SELECT status FROM postprocess_attempts WHERE algorithm_call_id = ? AND generation = ? ORDER BY attempt_number DESC LIMIT 1",
                (call_id, generation),
            ).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown Algorithm call: {call_id}")
        if existing and existing["status"] == "complete":
            return
        if row["run_status"] == "paused":
            # Pausing does not stop result collection or postprocess.
            pass
        with transaction(self.database_path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO postprocess_attempts(
                    id, algorithm_call_id, generation, attempt_number, status, config_json, started_at
                ) VALUES (?, ?, ?, 1, 'running', '{}', CURRENT_TIMESTAMP)
                """,
                (_id("post", call_id, generation, 1), call_id, generation),
            )
        with connect(self.database_path) as connection:
            postprocess_testcases = [
                {"id": testcase["id"], "name": testcase["name"], "casePath": testcase["case_path"]}
                for testcase in connection.execute(
                    "SELECT id, name, case_path FROM testcases WHERE algorithm_call_id = ? AND status != 'ignored'",
                    (call_id,),
                )
            ]
        try:
            result = self.postprocess.run(
                Path(row["directory_path"]), {"testcases": postprocess_testcases}
            )
        except Exception as exc:
            with transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE postprocess_attempts SET status = 'failed', error_json = ?, finished_at = CURRENT_TIMESTAMP WHERE algorithm_call_id = ? AND generation = ?",
                    (json.dumps({"type": type(exc).__name__, "message": str(exc)}), call_id, generation),
                )
                connection.execute(
                    "UPDATE stage_executions SET status = 'blocked', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["stage_id"],),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'waiting_user', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["run_corner_id"],),
                )
            raise PermanentWorkError(f"Postprocess failed: {exc}") from exc
        testcase_results = result.get("testcases", {})
        try:
            with transaction(self.database_path) as connection:
                testcases = connection.execute(
                    "SELECT id, name FROM testcases WHERE algorithm_call_id = ?", (call_id,)
                ).fetchall()
                for testcase in testcases:
                    metrics = testcase_results.get(testcase["id"], testcase_results.get(testcase["name"]))
                    if metrics is None and testcase["id"] not in testcase_results and testcase["name"] not in testcase_results:
                        raise CoordinatorError(f"postprocess did not return a result for {testcase['name']}")
                    connection.execute(
                        "UPDATE testcases SET result_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (json.dumps(metrics), testcase["id"]),
                    )
                connection.execute(
                    "UPDATE postprocess_attempts SET status = 'complete', result_json = ?, finished_at = CURRENT_TIMESTAMP WHERE algorithm_call_id = ? AND generation = ?",
                    (json.dumps(result), call_id, generation),
                )
                connection.execute(
                    "UPDATE stage_executions SET status = 'deciding', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["stage_id"],),
                )
        except Exception as exc:
            with transaction(self.database_path) as connection:
                connection.execute(
                    "UPDATE postprocess_attempts SET status = 'failed', error_json = ?, finished_at = CURRENT_TIMESTAMP WHERE algorithm_call_id = ? AND generation = ?",
                    (json.dumps({"type": type(exc).__name__, "message": str(exc)}), call_id, generation),
                )
                connection.execute(
                    "UPDATE stage_executions SET status = 'blocked', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["stage_id"],),
                )
                connection.execute(
                    "UPDATE run_corners SET status = 'waiting_user', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["run_corner_id"],),
                )
            raise PermanentWorkError(f"Postprocess result validation failed: {exc}") from exc
        if row["active"]:
            with connect(self.database_path) as connection:
                next_call = connection.execute(
                    "SELECT COALESCE(MAX(call_number), 0) + 1 FROM algorithm_calls WHERE stage_execution_id = ?",
                    (row["stage_id"],),
                ).fetchone()[0]
            self.queue.enqueue(
                f"decide:{row['stage_id']}:{next_call}", "decide_stage",
                {"stageExecutionId": row["stage_id"], "callNumber": next_call},
            )

    def _complete_run_if_ready(self, run_id: str) -> None:
        with transaction(self.database_path) as connection:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM run_corners WHERE run_id = ? AND status != 'done'", (run_id,)
            ).fetchone()[0]
            if remaining == 0:
                connection.execute(
                    "UPDATE runs SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (run_id,),
                )
