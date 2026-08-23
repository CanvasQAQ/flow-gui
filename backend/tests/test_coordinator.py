from pathlib import Path
import json
import sys

from flow_backend.adapters.hspice import HspiceSimulator
from flow_backend.adapters.lsf import LsfSubmissionUnknown
from flow_backend.adapters.copyback import CopyBackResult
from flow_backend.batch import BatchManifest, write_execution_status
from flow_backend.coordinator import WorkflowCoordinator
from flow_backend.db import connect, migrate, transaction
from flow_backend.runner import run_item
from flow_backend.services.runs import RunService
from flow_backend.services.recovery import CaseFilter, CaseRecoveryService
from flow_backend.services.rollback import RollbackService
from flow_backend.work_queue import DurableWorkQueue, PermanentWorkError, WorkProcessor
from flow_backend.workflow import AlgorithmDecision, TestcaseProposal as Proposal


class FakeAlgorithm:
    scheme_id = "fake"

    def stage_definitions(self):
        return [{"key": "search", "name": "Search", "final": True}]

    def decide(self, stage_key, payload):
        verified = payload["verifiedTestcases"]
        if not verified:
            return AlgorithmDecision(
                kind="add_testcases",
                reason="first proposal",
                testcases=(Proposal("tc_001", {"rangeselcode": 12}),),
            )
        return AlgorithmDecision(
            kind="complete", reason="enough", result={"bestCode": verified[0]["parameters"]}
        )


class FakeRegistry:
    def get(self, scheme_id):
        assert scheme_id == "fake"
        return FakeAlgorithm()


class FakeScheduler:
    def __init__(self):
        self.submit_calls = 0

    def submit(self, manifest_path, job_name, settings):
        self.submit_calls += 1
        return "12345"

    def cancel(self, job_id):
        pass


class FakePostprocess:
    def run(self, stage_directory, config):
        return {
            "status": "success",
            "metrics": {"count": 1},
            "testcases": {"tc_001": {"loss_0": 0.1, "loss_90": 0.2, "loss_180": 0.3, "loss_270": 0.4}},
            "files": [],
        }


class FakeCopyBack:
    def copy(self, request):
        return CopyBackResult("compute-01", str(request.return_path))


def seed_input(database: Path, root: Path) -> str:
    sp = root / "source.sp"
    mt = root / "source.mt"
    sp.write_text(
        "title\n.param rangeselcode=1 vdd=0.7 vcm=0.35\n.end\n", encoding="utf-8"
    )
    mt.write_text("rangesel loss_0\n1 0.5\n", encoding="utf-8")
    with transaction(database) as connection:
        connection.execute(
            "INSERT INTO datasets(id, name, source_path) VALUES ('ds1', 'Dataset', ?)", (str(root),)
        )
        connection.execute(
            "INSERT INTO corners(id, dataset_id, corner_number, corner_key, availability) VALUES ('c1', 'ds1', 1, '1', 'available')"
        )
        connection.execute(
            """
            INSERT INTO corner_input_versions(
                id, corner_id, version_number, sp_snapshot_path, mt_snapshot_path,
                content_fingerprint, initial_code_json, reference_metrics_json
            ) VALUES ('v1', 'c1', 1, ?, ?, 'hash', '{"rangesel": 1}', '{"loss_0": 0.5}')
            """,
            (str(sp), str(mt)),
        )
        connection.execute("UPDATE corners SET current_input_version_id = 'v1' WHERE id = 'c1'")
    return "ds1"


def test_fake_end_to_end_stage_survives_durable_boundaries(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    migrate(database)
    dataset_id = seed_input(database, tmp_path)
    run_service = RunService(database, tmp_path / "runs")
    created, _ = run_service.create(
        "create-run", dataset_id=dataset_id, name="Run", pvt={"vdd": 0.72, "vcm": 0.36},
        algorithm_scheme="fake", algorithm_config={}, corner_numbers=[1],
    )
    coordinator = WorkflowCoordinator(
        database, tmp_path / "runs", FakeRegistry(), HspiceSimulator(), FakeScheduler(),
        FakePostprocess(),
        [sys.executable, "-c", "from pathlib import Path; Path('wave.raw').write_text('ok')"],
        ["wave.raw"],
    )
    queue = DurableWorkQueue(database)
    processor = WorkProcessor(queue, coordinator.handlers)

    assert processor.tick()  # initialize Corner
    assert processor.tick()  # algorithm proposes Testcase and prepares Batch
    assert processor.tick()  # submit Batch
    assert not processor.tick()

    with connect(database) as connection:
        batch = connection.execute("SELECT manifest_path, status FROM batches").fetchone()
    assert batch["status"] == "submitted"
    manifest = BatchManifest.read(Path(batch["manifest_path"]))
    assert run_item(
        Path(batch["manifest_path"]), 1,
        scratch_root=tmp_path / "scratch", scratch_user="tester",
    ) == 0

    assert coordinator.reconcile() == {"changedAttempts": 1, "postprocessQueued": 1}
    assert processor.tick()  # Postprocess
    assert processor.tick()  # Algorithm completes the final Stage
    assert not processor.tick()

    with connect(database) as connection:
        run = connection.execute("SELECT status FROM runs WHERE id = ?", (created["runId"],)).fetchone()
        corner = connection.execute("SELECT status, final_result_json FROM run_corners").fetchone()
        calls = connection.execute("SELECT COUNT(*) FROM algorithm_calls").fetchone()[0]
    assert run["status"] == "completed"
    assert corner["status"] == "done"
    assert json.loads(corner["final_result_json"])["bestCode"] == {"rangeselcode": 12}
    assert calls == 2

    recovery = CaseRecoveryService(
        database,
        [sys.executable, "-c", "from pathlib import Path; Path('wave.raw').write_text('retry')"],
        ["wave.raw"],
        {},
    )
    testcase_id = manifest.items[0].testcase_id
    preview = recovery.preview(created["runId"], "resubmit", CaseFilter(testcase_ids=(testcase_id,)))
    confirmed, replayed = recovery.confirm(preview["token"], "resubmit-completed-case")
    assert not replayed
    assert confirmed["resubmittedCount"] == 1
    assert processor.tick()  # submit retry Batch
    retry_batch_id = confirmed["batchIds"][0]
    with connect(database) as connection:
        retry_manifest_path = connection.execute(
            "SELECT manifest_path FROM batches WHERE id = ?", (retry_batch_id,)
        ).fetchone()[0]
    assert run_item(
        Path(retry_manifest_path), 1,
        scratch_root=tmp_path / "scratch", scratch_user="tester",
    ) == 0
    assert coordinator.reconcile() == {"changedAttempts": 1, "postprocessQueued": 1}
    assert processor.tick()  # second Postprocess generation
    assert processor.tick()  # new algorithm decision
    with connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM postprocess_attempts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM algorithm_calls").fetchone()[0] == 3
        assert connection.execute("SELECT status FROM runs").fetchone()[0] == "completed"

    rollback = RollbackService(database)
    rollback_preview = rollback.preview(created["runId"], [1], "search")
    assert rollback_preview["eligibleCount"] == 1
    rollback_result, rollback_replayed = rollback.execute(
        "rollback-search-decision", created["runId"], [1], "search"
    )
    assert not rollback_replayed
    assert len(rollback_result["processed"]) == 1
    assert processor.tick()  # re-decide using preserved simulation and result data
    with connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM algorithm_calls").fetchone()[0] == 4
        assert connection.execute("SELECT status FROM runs").fetchone()[0] == "completed"


def test_status_unknown_batch_is_never_submitted_again(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    migrate(database)
    dataset_id = seed_input(database, tmp_path)
    created, _ = RunService(database, tmp_path / "runs").create(
        "create-run", dataset_id=dataset_id, name="Run", pvt={},
        algorithm_scheme="fake", algorithm_config={}, corner_numbers=[1],
    )
    scheduler = FakeScheduler()
    coordinator = WorkflowCoordinator(
        database, tmp_path / "runs", FakeRegistry(), HspiceSimulator(), scheduler,
        FakePostprocess(), [sys.executable, "-c", "pass"], [],
    )
    processor = WorkProcessor(coordinator.queue, coordinator.handlers)
    assert processor.tick()  # initialize
    assert processor.tick()  # prepare Batch
    with transaction(database) as connection:
        batch_id = connection.execute("SELECT id FROM batches").fetchone()[0]
        connection.execute(
            "UPDATE batches SET status = 'status_unknown' WHERE id = ?", (batch_id,)
        )

    coordinator.submit_batch({"batchId": batch_id})

    assert scheduler.submit_calls == 0
    with connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM batches WHERE id = ?", (batch_id,)
        ).fetchone()[0] == "status_unknown"


def test_copyback_recovery_and_abandon_then_resubmit(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    migrate(database)
    dataset_id = seed_input(database, tmp_path)
    created, _ = RunService(database, tmp_path / "runs").create(
        "create-run", dataset_id=dataset_id, name="Run", pvt={},
        algorithm_scheme="fake", algorithm_config={}, corner_numbers=[1],
    )
    coordinator = WorkflowCoordinator(
        database, tmp_path / "runs", FakeRegistry(), HspiceSimulator(), FakeScheduler(),
        FakePostprocess(), [sys.executable, "-c", "pass"], [],
    )
    processor = WorkProcessor(coordinator.queue, coordinator.handlers)
    assert processor.tick()
    assert processor.tick()
    with connect(database) as connection:
        row = connection.execute(
            """
            SELECT ea.id AS attempt_id, ea.batch_id, ea.array_index, ea.testcase_id,
                   t.case_path FROM execution_attempts ea
            JOIN testcases t ON t.id = ea.testcase_id
            """
        ).fetchone()
    case_path = Path(row["case_path"])
    scratch_path = tmp_path / "scratch" / "tester" / "flowpilot" / row["attempt_id"]
    return_path = case_path / ".flowpilot" / "attempts" / row["attempt_id"]
    return_path.mkdir(parents=True)
    (return_path / "compute-01").touch()
    write_execution_status(
        case_path / "status.json", state="copyback_waiting", batchId=row["batch_id"],
        testcaseId=row["testcase_id"], attemptId=row["attempt_id"],
        arrayIndex=row["array_index"], scratchPath=str(scratch_path),
        returnPath=str(return_path), simulatorExitCode=0, missingExpectedFiles=[],
    )
    with transaction(database) as connection:
        connection.execute(
            "UPDATE execution_attempts SET status='copyback_waiting', scratch_path=?, return_path=? WHERE id=?",
            (str(scratch_path), str(return_path), row["attempt_id"]),
        )
        connection.execute(
            "UPDATE testcases SET status='copyback_waiting' WHERE id=?", (row["testcase_id"],)
        )

    recovery = CaseRecoveryService(
        database, [sys.executable, "-c", "pass"], [], {}, copyback_adapter=FakeCopyBack()
    )
    copied = recovery.retry_copyback(row["attempt_id"])
    assert copied["status"] == "succeeded"
    status = json.loads((case_path / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "complete"
    assert status["hostname"] == "compute-01"

    # Put the same Attempt back in the copy-failed state to exercise the explicit
    # "abandon this Submit and run again" semantics of Recovery resubmit.
    with transaction(database) as connection:
        connection.execute(
            "UPDATE execution_attempts SET status='copyback_waiting' WHERE id=?",
            (row["attempt_id"],),
        )
        connection.execute(
            "UPDATE testcases SET status='copyback_waiting' WHERE id=?", (row["testcase_id"],)
        )
    preview = recovery.preview(
        created["runId"], "resubmit", CaseFilter(testcase_ids=(row["testcase_id"],))
    )
    result, replayed = recovery.confirm(preview["token"], "abandon-copyback-and-rerun")
    assert not replayed and result["resubmittedCount"] == 1
    with connect(database) as connection:
        old = connection.execute(
            "SELECT status, abandoned_at FROM execution_attempts WHERE id=?", (row["attempt_id"],)
        ).fetchone()
    assert old["status"] == "abandoned"
    assert old["abandoned_at"] is not None


def test_ambiguous_bsub_result_becomes_status_unknown(tmp_path: Path) -> None:
    class AmbiguousScheduler(FakeScheduler):
        def submit(self, manifest_path, job_name, settings):
            self.submit_calls += 1
            raise LsfSubmissionUnknown("accepted maybe")

    database = tmp_path / "db.sqlite3"
    migrate(database)
    dataset_id = seed_input(database, tmp_path)
    RunService(database, tmp_path / "runs").create(
        "create-run", dataset_id=dataset_id, name="Run", pvt={},
        algorithm_scheme="fake", algorithm_config={}, corner_numbers=[1],
    )
    scheduler = AmbiguousScheduler()
    coordinator = WorkflowCoordinator(
        database, tmp_path / "runs", FakeRegistry(), HspiceSimulator(), scheduler,
        FakePostprocess(), [sys.executable, "-c", "pass"], [],
    )
    processor = WorkProcessor(coordinator.queue, coordinator.handlers)
    assert processor.tick()
    assert processor.tick()
    with connect(database) as connection:
        batch_id = connection.execute("SELECT id FROM batches").fetchone()[0]

    try:
        coordinator.submit_batch({"batchId": batch_id})
    except PermanentWorkError:
        pass
    else:
        raise AssertionError("ambiguous submission must stop automatic processing")

    assert scheduler.submit_calls == 1
    with connect(database) as connection:
        assert connection.execute("SELECT status FROM batches").fetchone()[0] == "status_unknown"
        assert connection.execute("SELECT status FROM execution_attempts").fetchone()[0] == "status_unknown"
        assert connection.execute("SELECT status FROM testcases").fetchone()[0] == "status_unknown"
