from enum import StrEnum


class RunStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class CornerStatus(StrEnum):
    NOT_STARTED = "not_started"
    PREPARING = "preparing"
    QUEUED = "queued"
    RUNNING = "running"
    POSTPROCESS = "postprocess"
    WAITING_USER = "waiting_user"
    STATUS_UNKNOWN = "status_unknown"
    FAILED = "failed"
    DONE = "done"
    IGNORED = "ignored"


class AttemptStatus(StrEnum):
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    SUBMITTING = "submitting"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STATUS_UNKNOWN = "status_unknown"
    IGNORED = "ignored"


class BatchStatus(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FINISHED = "finished"
    STATUS_UNKNOWN = "status_unknown"


class DecisionKind(StrEnum):
    ADD_TESTCASES = "add_testcases"
    ADVANCE_STAGE = "advance_stage"
    COMPLETE = "complete"
    CANNOT_CONTINUE = "cannot_continue"

