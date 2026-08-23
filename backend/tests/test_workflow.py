from pathlib import Path

import pytest

from flow_backend.db import connect, migrate
from flow_backend.idempotency import IdempotencyConflict, IdempotencyStore
from flow_backend.work_queue import DurableWorkQueue, WorkProcessor
from flow_backend.workflow import (
    AlgorithmDecision,
    AttemptEvidence,
    TestcaseProposal as Proposal,
    WorkflowContractError,
    algorithm_group_ready,
    classify_attempt,
)


def test_idempotent_operation_replays_result_and_rejects_changed_payload(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    migrate(database)
    store = IdempotencyStore(database)

    def operation(connection):
        connection.execute("INSERT INTO datasets(id, name, source_path) VALUES ('d1', 'D', '/d')")
        return {"id": "d1"}

    assert store.perform("key", "create", {"name": "D"}, operation) == ({"id": "d1"}, False)
    assert store.perform("key", "create", {"name": "D"}, operation) == ({"id": "d1"}, True)
    with pytest.raises(IdempotencyConflict):
        store.perform("key", "create", {"name": "changed"}, operation)


def test_durable_work_queue_deduplicates_and_processes(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    migrate(database)
    queue = DurableWorkQueue(database)
    first = queue.enqueue("corner:1", "advance", {"corner": 1})
    assert queue.enqueue("corner:1", "advance", {"corner": 1}) == first
    seen = []
    processor = WorkProcessor(queue, {"advance": lambda payload: seen.append(payload["corner"])})
    assert processor.tick() is True
    assert processor.tick() is False
    assert seen == [1]
    with connect(database) as connection:
        assert connection.execute("SELECT status FROM work_items").fetchone()[0] == "complete"


def test_workflow_contract_and_evidence_rules() -> None:
    decision = AlgorithmDecision(
        kind="add_testcases",
        reason="sample",
        testcases=(Proposal("tc_1", {"rangeselcode": 12}),),
    )
    decision.validate(["stage1", "final"])
    with pytest.raises(WorkflowContractError):
        AlgorithmDecision(kind="advance_stage", reason="", next_stage="missing").validate(["stage1"])

    assert classify_attempt(AttemptEvidence("complete", True, "DONE", True)) == "succeeded"
    assert classify_attempt(AttemptEvidence(None, False, "DONE", True)) == "status_unknown"
    assert classify_attempt(AttemptEvidence(None, False, "RUN", False)) == "running"
    assert algorithm_group_ready(["succeeded", "ignored"])
    assert not algorithm_group_ready(["succeeded", "failed"])
