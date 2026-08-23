from pathlib import Path

import duckdb

from flow_backend.adapters.snapshot import ParquetSchedulerSnapshot, SnapshotColumns


def test_parquet_snapshot_uses_configured_site_columns(tmp_path: Path) -> None:
    parquet = tmp_path / "bjobs.parquet"
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    ('123', 'flow_a', 1, 'RUN', NULL),
                    ('124', 'other', 1, 'PEND', NULL)
                ) AS jobs(JOBID, JOB_NAME, JOB_INDEX, STAT, EXIT_CODE)
            ) TO ? (FORMAT PARQUET)
            """,
            [str(parquet)],
        )
    finally:
        connection.close()

    reader = ParquetSchedulerSnapshot(
        parquet,
        SnapshotColumns(
            job_id="JOBID", job_name="JOB_NAME", array_index="JOB_INDEX",
            status="STAT", exit_code="EXIT_CODE",
        ),
    )
    rows = reader.read_jobs(["flow_a"])
    assert reader.snapshot_identity() is not None
    assert rows == [{
        "jobId": "123", "jobName": "flow_a", "status": "RUN",
        "arrayIndex": 1, "exitCode": None,
    }]

