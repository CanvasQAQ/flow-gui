from pathlib import Path
from threading import Event
import time

from flow_backend.db import connect, migrate
from flow_backend.runtime import RuntimeWorkerSupervisor
from flow_backend.work_queue import DurableWorkQueue


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_blocking_submission_does_not_block_postprocess_lane(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    migrate(database)
    queue = DurableWorkQueue(database)
    submission_started = Event()
    release_submission = Event()
    postprocessed = Event()

    def submit(_payload):
        submission_started.set()
        assert release_submission.wait(2)

    handlers = {
        "submit_batch": submit,
        "run_postprocess": lambda _payload: postprocessed.set(),
        "initialize_run_corner": lambda _payload: None,
        "decide_stage": lambda _payload: None,
    }
    queue.enqueue("submit:1", "submit_batch", {})
    queue.enqueue("post:1", "run_postprocess", {})
    supervisor = RuntimeWorkerSupervisor(
        database, handlers, RuntimeWorkerSupervisor.workflow_defaults()
    )
    supervisor.start()
    try:
        assert submission_started.wait(1)
        assert postprocessed.wait(1), "Postprocess was blocked by the submission lane"
        assert supervisor.health()["submission"]["active"] == 1
        release_submission.set()
        _wait_until(lambda: supervisor.health()["submission"]["completed"] == 1)
    finally:
        release_submission.set()
        assert supervisor.stop(2)

    with connect(database) as connection:
        statuses = {
            row["work_type"]: row["status"]
            for row in connection.execute("SELECT work_type, status FROM work_items")
        }
    assert statuses == {"submit_batch": "complete", "run_postprocess": "complete"}


def test_lane_configuration_rejects_overlapping_work_types(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    migrate(database)
    lanes = RuntimeWorkerSupervisor.workflow_defaults()
    try:
        RuntimeWorkerSupervisor(database, {name: lambda _: None for name in lanes[0].work_types}, lanes)
    except ValueError as exc:
        assert "handlers missing" in str(exc)
    else:
        raise AssertionError("missing handlers were accepted")
