from datetime import datetime, timedelta, timezone
from pathlib import Path

from flow_backend.services.status_monitor import (
    MemorySnapshotIdentityStore,
    SqliteSnapshotIdentityStore,
    SqliteRuntimeHealthSink,
    SqliteStatusMonitorStore,
    StatusCandidate,
    StatusMonitor,
)
from flow_backend.db import connect, migrate


class FakeStore:
    def __init__(self, candidates=()):
        self.candidates = list(candidates)
        self.claim_limits = []
        self.observations = []
        self.failures = []

    def claim_due(self, **kwargs):
        self.claim_limits.append(kwargs["limit"])
        claimed, self.candidates = self.candidates[: kwargs["limit"]], self.candidates[kwargs["limit"] :]
        return claimed

    def record_observation(self, candidate, fingerprint, document, **kwargs):
        self.observations.append((candidate, fingerprint, document, kwargs))

    def record_failure(self, candidate, error, **kwargs):
        self.failures.append((candidate, error, kwargs))


class FakeSnapshot:
    def __init__(self):
        self.identity = "snapshot-1"
        self.identity_calls = 0
        self.read_calls = []

    def snapshot_identity(self):
        self.identity_calls += 1
        return self.identity

    def read_jobs(self, names):
        self.read_calls.append(tuple(names))
        return [{"jobName": name, "status": "RUN"} for name in names]


def test_monitor_checks_bounded_exact_paths_and_skips_unchanged_json(tmp_path: Path) -> None:
    changed = tmp_path / "one" / "status.json"
    changed.parent.mkdir()
    changed.write_text(
        '{"schemaVersion":1,"attemptId":"a1","state":"running"}', encoding="utf-8"
    )
    unchanged = tmp_path / "two" / "status.json"
    unchanged.parent.mkdir()
    unchanged.write_text("this is deliberately not JSON", encoding="utf-8")
    unchanged_stat = unchanged.stat()
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    store = FakeStore(
        [
            StatusCandidate("a1", changed, "job-1", 1, due_at=now),
            StatusCandidate(
                "a2",
                unchanged,
                "job-2",
                1,
                previous_mtime_ns=unchanged_stat.st_mtime_ns,
                previous_size=unchanged_stat.st_size,
                due_at=now,
            ),
        ]
    )
    monitor = StatusMonitor(store, chunk_size=2, interval_seconds=600, now=lambda: now)
    health = monitor.run_due_chunk()

    assert store.claim_limits == [2]
    assert not store.failures
    assert store.observations[0][2]["state"] == "running"
    assert store.observations[1][2] is None
    assert store.observations[0][3]["next_check_at"] == now + timedelta(seconds=600)
    assert health["checked"] == 2
    assert health["changed"] == 1
    assert health["unchanged"] == 1


def test_snapshot_duckdb_hook_runs_only_when_identity_changes() -> None:
    clock = [datetime(2026, 8, 23, tzinfo=timezone.utc)]
    snapshot = FakeSnapshot()
    identities = MemorySnapshotIdentityStore()
    received = []
    monitor = StatusMonitor(
        FakeStore(),
        interval_seconds=600,
        snapshot_reader=snapshot,
        snapshot_identity_store=identities,
        active_job_names=lambda: ["job-a", "job-b"],
        snapshot_sink=lambda identity, rows: received.append((identity, list(rows))),
        now=lambda: clock[0],
    )

    monitor.run_due_chunk()
    assert snapshot.read_calls == [("job-a", "job-b")]
    clock[0] += timedelta(seconds=601)
    monitor.run_due_chunk()
    assert len(snapshot.read_calls) == 1
    snapshot.identity = "snapshot-2"
    clock[0] += timedelta(seconds=601)
    health = monitor.run_due_chunk()
    assert len(snapshot.read_calls) == 2
    assert health["snapshotQueries"] == 2
    assert identities.load() == "snapshot-2"


def test_monitor_reports_file_error_without_crashing_worker(tmp_path: Path) -> None:
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    candidate = StatusCandidate("missing", tmp_path / "missing.json", "job", 1, due_at=now)
    store = FakeStore([candidate])
    health = StatusMonitor(store, now=lambda: now).run_due_chunk()
    assert len(store.failures) == 1
    assert health["state"] == "error"
    assert health["consecutiveFailures"] == 1


def test_sqlite_store_leases_and_applies_runner_state(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite3"
    case = tmp_path / "case"
    case.mkdir()
    (case / "status.json").write_text(
        '{"schemaVersion":1,"attemptId":"attempt","state":"complete",'
        '"missingExpectedFiles":[]}',
        encoding="utf-8",
    )
    migrate(database)
    with connect(database) as connection:
        connection.execute("INSERT INTO datasets(id,name,source_path) VALUES('d','D','/d')")
        connection.execute(
            "INSERT INTO corners(id,dataset_id,corner_number,corner_key,availability) "
            "VALUES('c','d',1,'1','available')"
        )
        connection.execute(
            """INSERT INTO corner_input_versions(
                id,corner_id,version_number,sp_snapshot_path,content_fingerprint
               ) VALUES('v','c',1,'/input.sp','fingerprint')"""
        )
        connection.execute("UPDATE corners SET current_input_version_id='v' WHERE id='c'")
        connection.execute(
            """INSERT INTO runs(id,dataset_id,name,status,algorithm_scheme,pvt_json)
               VALUES('r','d','R','running','algo','{}')"""
        )
        connection.execute(
            """INSERT INTO run_corners(id,run_id,corner_id,input_version_id,status)
               VALUES('rc','r','c','v','queued')"""
        )
        connection.execute(
            """INSERT INTO stage_executions(
                id,run_corner_id,stage_key,execution_number,status,directory_path
               ) VALUES('stage','rc','s',1,'simulating',?)""",
            (str(tmp_path),),
        )
        connection.execute(
            """INSERT INTO algorithm_calls(
                id,stage_execution_id,call_number,algorithm_name,input_json
               ) VALUES('call','stage',1,'algo','{}')"""
        )
        connection.execute(
            """INSERT INTO testcases(
                id,algorithm_call_id,stage_execution_id,name,parameters_json,case_path,sp_path,status
               ) VALUES('tc','call','stage','tc','{}',?,?,'queued')""",
            (str(case), str(case / "test.sp")),
        )
        connection.execute(
            """INSERT INTO batches(
                id,run_id,run_corner_id,stage_execution_id,algorithm_call_id,
                unique_job_name,submission_type,status,manifest_path,scheduler_settings_json
               ) VALUES('batch','r','rc','stage','call','job','job','submitted','/m','{}')"""
        )
        connection.execute(
            """INSERT INTO execution_attempts(
                id,testcase_id,attempt_number,reason,status,batch_id,array_index
               ) VALUES('attempt','tc',1,'test','pending','batch',1)"""
        )
        connection.commit()

    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    store = SqliteStatusMonitorStore(database)
    monitor = StatusMonitor(
        store,
        interval_seconds=600,
        health_sink=SqliteRuntimeHealthSink(database),
        now=lambda: now,
    )
    monitor.run_due_chunk()
    with connect(database) as connection:
        row = connection.execute(
            """SELECT status,next_check_at,status_mtime_ns,status_size,
                      monitor_lease_owner,monitor_error_json,evidence_json
               FROM execution_attempts WHERE id='attempt'"""
        ).fetchone()
        testcase_status = connection.execute(
            "SELECT status FROM testcases WHERE id='tc'"
        ).fetchone()[0]
        checker = connection.execute(
            "SELECT state,processed_count FROM runtime_components WHERE name='status_checker'"
        ).fetchone()
    assert row["status"] == testcase_status == "succeeded"
    assert row["next_check_at"] is not None
    assert row["status_mtime_ns"] is not None and row["status_size"] > 0
    assert row["monitor_lease_owner"] is None and row["monitor_error_json"] is None
    assert "statusFingerprint" in row["evidence_json"]
    assert tuple(checker) == ("normal", 1)

    identity_store = SqliteSnapshotIdentityStore(database)
    assert identity_store.load() is None
    identity_store.save("identity-1")
    assert identity_store.load() == "identity-1"
