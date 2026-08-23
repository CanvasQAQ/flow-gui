from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence


class WorkflowContractError(ValueError):
    pass


@dataclass(frozen=True)
class TestcaseProposal:
    name: str
    parameters: Mapping[str, Any]
    reason: str = ""


@dataclass(frozen=True)
class AlgorithmDecision:
    kind: Literal["add_testcases", "advance_stage", "complete", "cannot_continue"]
    reason: str
    testcases: tuple[TestcaseProposal, ...] = ()
    result: Mapping[str, Any] | None = None
    next_stage: str | None = None

    def validate(self, known_stages: Sequence[str]) -> None:
        if self.kind == "add_testcases":
            if not self.testcases:
                raise WorkflowContractError("add_testcases requires at least one testcase")
            names = [testcase.name for testcase in self.testcases]
            if len(names) != len(set(names)):
                raise WorkflowContractError("algorithm returned duplicate testcase names")
            if self.next_stage is not None:
                raise WorkflowContractError("add_testcases cannot specify next_stage")
        elif self.kind == "advance_stage":
            if not self.next_stage or self.next_stage not in known_stages:
                raise WorkflowContractError("advance_stage requires a known next_stage")
            if self.testcases:
                raise WorkflowContractError("advance_stage cannot also add testcases")
        elif self.kind == "complete":
            if self.next_stage is not None or self.testcases:
                raise WorkflowContractError("complete cannot specify next work")
        elif self.kind == "cannot_continue":
            if not self.reason:
                raise WorkflowContractError("cannot_continue requires a reason")


@dataclass(frozen=True)
class AttemptEvidence:
    status_state: str | None
    status_attempt_matches: bool
    scheduler_state: str | None
    expected_outputs_complete: bool


def classify_attempt(evidence: AttemptEvidence) -> str:
    """Merge evidence without treating scheduler disappearance as failure."""
    if evidence.status_attempt_matches and evidence.status_state == "complete":
        return "succeeded" if evidence.expected_outputs_complete else "failed"
    if evidence.status_attempt_matches and evidence.status_state in {"failed", "simulation_failed"}:
        return "failed"
    if evidence.status_attempt_matches and evidence.status_state == "copyback_waiting":
        return "copyback_waiting"
    if evidence.status_attempt_matches and evidence.status_state in {
        "submit", "staging_in", "running", "staging_out",
    }:
        return "running"
    scheduler = (evidence.scheduler_state or "").upper()
    if scheduler in {"PEND", "WAIT", "PSUSP"}:
        return "pending"
    if scheduler in {"RUN", "USUSP", "SSUSP"}:
        return "running"
    if scheduler in {"EXIT", "UNKWN", "ZOMBI"}:
        return "failed" if scheduler == "EXIT" else "status_unknown"
    if scheduler == "DONE":
        return "status_unknown"
    return "status_unknown"


def algorithm_group_ready(statuses: Sequence[str]) -> bool:
    return bool(statuses) and all(status in {"succeeded", "ignored"} for status in statuses)
