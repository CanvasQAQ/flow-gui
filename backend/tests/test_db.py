from pathlib import Path
import sqlite3

from flow_backend.db import SCHEMA_VERSION, connect, migrate, transaction


def test_migration_is_repeatable(tmp_path: Path) -> None:
    database = tmp_path / "flowpilot.sqlite3"
    migrate(database)
    migrate(database)

    with connect(database) as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert version == SCHEMA_VERSION
    assert {
        "datasets",
        "corners",
        "corner_input_versions",
        "runs",
        "run_corners",
        "stage_executions",
        "algorithm_calls",
        "testcases",
        "execution_attempts",
        "batches",
        "batch_items",
        "postprocess_runs",
        "operation_requests",
        "dataset_scans",
        "workflow_events",
        "scheduler_observations",
        "work_items",
        "recovery_previews",
        "postprocess_attempts",
    } <= tables


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    database = tmp_path / "flowpilot.sqlite3"
    migrate(database)

    try:
        with transaction(database) as connection:
            connection.execute(
                "INSERT INTO datasets(id, name, source_path) VALUES ('d1', 'Dataset', '/input')"
            )
            raise RuntimeError("stop")
    except RuntimeError:
        pass

    with connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    database = tmp_path / "flowpilot.sqlite3"
    migrate(database)

    with connect(database) as connection:
        try:
            connection.execute(
                "INSERT INTO runs(id, dataset_id, name, status, algorithm_scheme, pvt_json) "
                "VALUES ('r1', 'missing', 'Run', 'draft', 'algo', '{}')"
            )
        except sqlite3.IntegrityError:
            return
    raise AssertionError("foreign keys were not enforced")
