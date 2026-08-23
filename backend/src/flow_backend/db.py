from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 7

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    scan_status TEXT NOT NULL DEFAULT 'pending',
    scanned_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS corners (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    corner_number INTEGER NOT NULL,
    corner_key TEXT NOT NULL,
    availability TEXT NOT NULL,
    current_input_version_id TEXT,
    error_json TEXT,
    last_checked_at TEXT,
    UNIQUE(dataset_id, corner_number),
    UNIQUE(dataset_id, corner_key)
);

CREATE TABLE IF NOT EXISTS corner_input_versions (
    id TEXT PRIMARY KEY,
    corner_id TEXT NOT NULL REFERENCES corners(id),
    version_number INTEGER NOT NULL,
    sp_snapshot_path TEXT NOT NULL,
    mt_snapshot_path TEXT,
    content_fingerprint TEXT NOT NULL,
    initial_code_json TEXT,
    reference_metrics_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(corner_id, version_number),
    UNIQUE(corner_id, content_fingerprint)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    algorithm_scheme TEXT NOT NULL,
    algorithm_config_json TEXT NOT NULL DEFAULT '{}',
    pvt_json TEXT NOT NULL,
    paused_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_corners (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    corner_id TEXT NOT NULL REFERENCES corners(id),
    input_version_id TEXT NOT NULL REFERENCES corner_input_versions(id),
    status TEXT NOT NULL DEFAULT 'not_started',
    active_stage_execution_id TEXT,
    final_result_json TEXT,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, corner_id)
);

CREATE TABLE IF NOT EXISTS stage_executions (
    id TEXT PRIMARY KEY,
    run_corner_id TEXT NOT NULL REFERENCES run_corners(id),
    stage_key TEXT NOT NULL,
    execution_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    directory_path TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    superseded_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_corner_id, stage_key, execution_number)
);

CREATE TABLE IF NOT EXISTS algorithm_calls (
    id TEXT PRIMARY KEY,
    stage_execution_id TEXT NOT NULL REFERENCES stage_executions(id),
    call_number INTEGER NOT NULL,
    algorithm_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    decision_kind TEXT,
    reason TEXT,
    applied_at TEXT,
    superseded_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stage_execution_id, call_number)
);

CREATE TABLE IF NOT EXISTS testcases (
    id TEXT PRIMARY KEY,
    algorithm_call_id TEXT NOT NULL REFERENCES algorithm_calls(id),
    stage_execution_id TEXT NOT NULL REFERENCES stage_executions(id),
    name TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    case_path TEXT NOT NULL,
    sp_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started',
    ignored_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stage_execution_id, name)
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    run_corner_id TEXT NOT NULL REFERENCES run_corners(id),
    stage_execution_id TEXT NOT NULL REFERENCES stage_executions(id),
    algorithm_call_id TEXT NOT NULL REFERENCES algorithm_calls(id),
    unique_job_name TEXT NOT NULL UNIQUE,
    submission_type TEXT NOT NULL CHECK(submission_type IN ('job', 'array')),
    status TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    scheduler_settings_json TEXT NOT NULL,
    lsf_job_id TEXT,
    submission_receipt TEXT,
    submitted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS execution_attempts (
    id TEXT PRIMARY KEY,
    testcase_id TEXT NOT NULL REFERENCES testcases(id),
    attempt_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    batch_id TEXT REFERENCES batches(id),
    array_index INTEGER,
    lsf_job_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(testcase_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL REFERENCES batches(id),
    attempt_id TEXT NOT NULL REFERENCES execution_attempts(id),
    array_index INTEGER NOT NULL,
    PRIMARY KEY(batch_id, array_index),
    UNIQUE(attempt_id)
);

CREATE TABLE IF NOT EXISTS postprocess_runs (
    id TEXT PRIMARY KEY,
    algorithm_call_id TEXT NOT NULL REFERENCES algorithm_calls(id),
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(algorithm_call_id)
);

CREATE TABLE IF NOT EXISTS operation_requests (
    idempotency_key TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_corners_status ON run_corners(run_id, status);
CREATE INDEX IF NOT EXISTS idx_stage_active ON stage_executions(run_corner_id, active);
CREATE INDEX IF NOT EXISTS idx_testcases_status ON testcases(stage_execution_id, status);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON execution_attempts(status);
CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);
"""

MIGRATION_002 = """
ALTER TABLE datasets ADD COLUMN scan_config_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE dataset_scans (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    mode TEXT NOT NULL CHECK(mode IN ('append', 'overwrite')),
    status TEXT NOT NULL,
    summary_json TEXT,
    error_json TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES runs(id),
    run_corner_id TEXT REFERENCES run_corners(id),
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE scheduler_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES batches(id),
    attempt_id TEXT REFERENCES execution_attempts(id),
    snapshot_identity TEXT NOT NULL,
    snapshot_time TEXT,
    observed_status TEXT,
    raw_json TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, attempt_id, snapshot_identity)
);

CREATE TABLE work_items (
    id TEXT PRIMARY KEY,
    unique_key TEXT NOT NULL UNIQUE,
    work_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TEXT,
    finished_at TEXT,
    last_error_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_dataset_scans_dataset ON dataset_scans(dataset_id, started_at);
CREATE INDEX idx_events_run ON workflow_events(run_id, id);
CREATE INDEX idx_scheduler_observations_batch ON scheduler_observations(batch_id, observed_at);
CREATE INDEX idx_work_items_ready ON work_items(status, available_at);
"""

MIGRATION_003 = """
ALTER TABLE testcases ADD COLUMN result_json TEXT;
ALTER TABLE stage_executions ADD COLUMN result_json TEXT;
ALTER TABLE batches ADD COLUMN submission_error_json TEXT;
"""

MIGRATION_004 = """
CREATE TABLE recovery_previews (
    token TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    action TEXT NOT NULL CHECK(action IN ('resubmit', 'ignore')),
    selection_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE INDEX idx_recovery_previews_expiry ON recovery_previews(expires_at);
"""

MIGRATION_005 = """
CREATE TABLE postprocess_attempts (
    id TEXT PRIMARY KEY,
    algorithm_call_id TEXT NOT NULL REFERENCES algorithm_calls(id),
    generation INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(algorithm_call_id, generation, attempt_number)
);

CREATE INDEX idx_postprocess_attempts_call ON postprocess_attempts(algorithm_call_id, generation);
"""

MIGRATION_006 = """
ALTER TABLE batches ADD COLUMN submission_started_at TEXT;

ALTER TABLE execution_attempts ADD COLUMN next_check_at TEXT;
ALTER TABLE execution_attempts ADD COLUMN last_checked_at TEXT;
ALTER TABLE execution_attempts ADD COLUMN status_mtime_ns INTEGER;
ALTER TABLE execution_attempts ADD COLUMN status_size INTEGER;
ALTER TABLE execution_attempts ADD COLUMN scratch_path TEXT;
ALTER TABLE execution_attempts ADD COLUMN return_path TEXT;
ALTER TABLE execution_attempts ADD COLUMN execution_host TEXT;
ALTER TABLE execution_attempts ADD COLUMN copyback_error_json TEXT;
ALTER TABLE execution_attempts ADD COLUMN abandoned_at TEXT;
ALTER TABLE execution_attempts ADD COLUMN monitor_lease_owner TEXT;
ALTER TABLE execution_attempts ADD COLUMN monitor_lease_until TEXT;
ALTER TABLE execution_attempts ADD COLUMN monitor_error_json TEXT;

CREATE TABLE runtime_components (
    name TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    processed_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    last_heartbeat_at TEXT,
    last_success_at TEXT,
    next_due_at TEXT,
    error_json TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE runtime_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_attempts_next_check ON execution_attempts(status, next_check_at);
CREATE INDEX idx_attempt_monitor_due
    ON execution_attempts(status, next_check_at, monitor_lease_until);

CREATE TRIGGER event_dataset_insert AFTER INSERT ON datasets BEGIN
    INSERT INTO workflow_events(event_type, entity_type, entity_id)
    VALUES ('entity_changed', 'dataset', NEW.id);
END;
CREATE TRIGGER event_dataset_update AFTER UPDATE ON datasets BEGIN
    INSERT INTO workflow_events(event_type, entity_type, entity_id)
    VALUES ('entity_changed', 'dataset', NEW.id);
END;
CREATE TRIGGER event_run_insert AFTER INSERT ON runs BEGIN
    INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id)
    VALUES (NEW.id, 'entity_changed', 'run', NEW.id);
END;
CREATE TRIGGER event_run_update AFTER UPDATE ON runs BEGIN
    INSERT INTO workflow_events(run_id, event_type, entity_type, entity_id)
    VALUES (NEW.id, 'entity_changed', 'run', NEW.id);
END;
CREATE TRIGGER event_corner_insert AFTER INSERT ON run_corners BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    VALUES (NEW.run_id, NEW.id, 'entity_changed', 'run_corner', NEW.id);
END;
CREATE TRIGGER event_corner_update AFTER UPDATE ON run_corners BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    VALUES (NEW.run_id, NEW.id, 'entity_changed', 'run_corner', NEW.id);
END;
CREATE TRIGGER event_stage_insert AFTER INSERT ON stage_executions BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    SELECT rc.run_id, NEW.run_corner_id, 'entity_changed', 'stage_execution', NEW.id
    FROM run_corners rc WHERE rc.id = NEW.run_corner_id;
END;
CREATE TRIGGER event_stage_update AFTER UPDATE ON stage_executions BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    SELECT rc.run_id, NEW.run_corner_id, 'entity_changed', 'stage_execution', NEW.id
    FROM run_corners rc WHERE rc.id = NEW.run_corner_id;
END;
CREATE TRIGGER event_batch_insert AFTER INSERT ON batches BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    VALUES (NEW.run_id, NEW.run_corner_id, 'entity_changed', 'batch', NEW.id);
END;
CREATE TRIGGER event_batch_update AFTER UPDATE ON batches BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    VALUES (NEW.run_id, NEW.run_corner_id, 'entity_changed', 'batch', NEW.id);
END;
CREATE TRIGGER event_attempt_insert AFTER INSERT ON execution_attempts BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    SELECT b.run_id, b.run_corner_id, 'entity_changed', 'execution_attempt', NEW.id
    FROM batches b WHERE b.id = NEW.batch_id;
END;
CREATE TRIGGER event_attempt_update AFTER UPDATE ON execution_attempts
WHEN OLD.status IS NOT NEW.status
  OR OLD.evidence_json IS NOT NEW.evidence_json
  OR OLD.copyback_error_json IS NOT NEW.copyback_error_json
  OR OLD.abandoned_at IS NOT NEW.abandoned_at
BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    SELECT b.run_id, b.run_corner_id, 'entity_changed', 'execution_attempt', NEW.id
    FROM batches b WHERE b.id = NEW.batch_id;
END;
CREATE TRIGGER event_postprocess_insert AFTER INSERT ON postprocess_attempts BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    SELECT rc.run_id, se.run_corner_id, 'entity_changed', 'postprocess_attempt', NEW.id
    FROM algorithm_calls ac
    JOIN stage_executions se ON se.id = ac.stage_execution_id
    JOIN run_corners rc ON rc.id = se.run_corner_id
    WHERE ac.id = NEW.algorithm_call_id;
END;
CREATE TRIGGER event_postprocess_update AFTER UPDATE ON postprocess_attempts BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    SELECT rc.run_id, se.run_corner_id, 'entity_changed', 'postprocess_attempt', NEW.id
    FROM algorithm_calls ac
    JOIN stage_executions se ON se.id = ac.stage_execution_id
    JOIN run_corners rc ON rc.id = se.run_corner_id
    WHERE ac.id = NEW.algorithm_call_id;
END;
"""

MIGRATION_007 = """
CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_attempt_monitor_due
    ON execution_attempts(status, next_check_at, monitor_lease_until);

DROP TRIGGER IF EXISTS event_attempt_update;
CREATE TRIGGER event_attempt_update AFTER UPDATE ON execution_attempts
WHEN OLD.status IS NOT NEW.status
  OR OLD.evidence_json IS NOT NEW.evidence_json
  OR OLD.copyback_error_json IS NOT NEW.copyback_error_json
  OR OLD.abandoned_at IS NOT NEW.abandoned_at
BEGIN
    INSERT INTO workflow_events(run_id, run_corner_id, event_type, entity_type, entity_id)
    SELECT b.run_id, b.run_corner_id, 'entity_changed', 'execution_attempt', NEW.id
    FROM batches b WHERE b.id = NEW.batch_id;
END;
"""


_ATTEMPT_MONITOR_COLUMNS = {
    "monitor_lease_owner": "TEXT",
    "monitor_lease_until": "TEXT",
    "monitor_error_json": "TEXT",
}


def connect(database_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def migrate(database_path: Path | str) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(MIGRATION_001)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)",
        )
        applied = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if 2 not in applied:
            connection.executescript(MIGRATION_002)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
            applied.add(2)
        if 3 not in applied:
            connection.executescript(MIGRATION_003)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
            applied.add(3)
        if 4 not in applied:
            connection.executescript(MIGRATION_004)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (4)")
            applied.add(4)
        if 5 not in applied:
            connection.executescript(MIGRATION_005)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (5)")
            applied.add(5)
        if 6 not in applied:
            connection.executescript(MIGRATION_006)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (6)")
            applied.add(6)
        if 7 not in applied:
            existing_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(execution_attempts)")
            }
            for name, column_type in _ATTEMPT_MONITOR_COLUMNS.items():
                if name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE execution_attempts ADD COLUMN {name} {column_type}"
                    )
            connection.executescript(MIGRATION_007)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (7)")


@contextmanager
def transaction(database_path: Path | str) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
