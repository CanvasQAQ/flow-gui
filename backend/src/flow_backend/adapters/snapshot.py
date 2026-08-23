from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import re


class SnapshotError(RuntimeError):
    pass


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SnapshotColumns:
    job_id: str
    job_name: str
    status: str
    array_index: str | None = None
    exit_code: str | None = None
    execution_host: str | None = None
    submitted_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ParquetSchedulerSnapshot:
    def __init__(self, path: Path, columns: SnapshotColumns):
        self.path = path
        self.columns = columns
        for value in columns.__dict__.values():
            if value is not None and not _IDENTIFIER.fullmatch(value):
                raise SnapshotError(f"unsafe snapshot column name: {value!r}")

    def snapshot_identity(self) -> str | None:
        if not self.path.is_file():
            return None
        stat = self.path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def read_jobs(self, job_names: Sequence[str]) -> Sequence[dict]:
        if not job_names or not self.path.is_file():
            return []
        try:
            import duckdb
        except ImportError as exc:
            raise SnapshotError("install flow-backend[parquet] to read bjobs.parquet") from exc

        selected = {
            "jobId": self.columns.job_id,
            "jobName": self.columns.job_name,
            "status": self.columns.status,
            "arrayIndex": self.columns.array_index,
            "exitCode": self.columns.exit_code,
            "executionHost": self.columns.execution_host,
            "submittedAt": self.columns.submitted_at,
            "startedAt": self.columns.started_at,
            "finishedAt": self.columns.finished_at,
        }
        expressions = [f'"{column}" AS "{alias}"' for alias, column in selected.items() if column]
        placeholders = ",".join("?" for _ in job_names)
        query = (
            f"SELECT {', '.join(expressions)} FROM read_parquet(?) "
            f'WHERE "{self.columns.job_name}" IN ({placeholders})'
        )
        connection = duckdb.connect(":memory:")
        try:
            cursor = connection.execute(query, [str(self.path), *job_names])
            names = [description[0] for description in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        finally:
            connection.close()

