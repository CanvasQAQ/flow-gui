from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from .files import atomic_write_json


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class BatchItem:
    index: int
    testcase_id: str
    attempt_id: str
    case_path: str
    sp_path: str
    status_path: str
    return_path: str | None = None


@dataclass(frozen=True)
class BatchManifest:
    schema_version: int
    batch_id: str
    job_name: str
    created_at: str
    command: tuple[str, ...]
    expected_files: tuple[str, ...]
    items: tuple[BatchItem, ...]

    @classmethod
    def create(
        cls,
        batch_id: str,
        job_name: str,
        command: list[str] | tuple[str, ...],
        items: list[BatchItem] | tuple[BatchItem, ...],
        expected_files: list[str] | tuple[str, ...] = (),
    ) -> "BatchManifest":
        manifest = cls(
            schema_version=2,
            batch_id=batch_id,
            job_name=job_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            command=tuple(command),
            expected_files=tuple(expected_files),
            items=tuple(items),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ManifestError(f"unsupported manifest schema: {self.schema_version}")
        if not self.command:
            raise ManifestError("simulator command must not be empty")
        if not self.items:
            raise ManifestError("batch must contain at least one item")
        indices = [item.index for item in self.items]
        if indices != list(range(1, len(self.items) + 1)):
            raise ManifestError("batch item indices must be contiguous and start at 1")
        if len({item.attempt_id for item in self.items}) != len(self.items):
            raise ManifestError("attempt IDs must be unique in a batch")
        for item in self.items:
            case_path = Path(item.case_path)
            if not case_path.is_absolute() or not Path(item.sp_path).is_absolute():
                raise ManifestError("case and SP paths must be absolute")
            if not Path(item.status_path).is_absolute():
                raise ManifestError("status path must be absolute")
            if item.return_path is not None and not Path(item.return_path).is_absolute():
                raise ManifestError("Attempt return path must be absolute")

    def write_once(self, path: Path) -> None:
        if path.exists():
            raise ManifestError(f"manifest already exists and is immutable: {path}")
        atomic_write_json(path, asdict(self))

    @classmethod
    def read(cls, path: Path) -> "BatchManifest":
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            manifest = cls(
                schema_version=raw["schema_version"],
                batch_id=raw["batch_id"],
                job_name=raw["job_name"],
                created_at=raw["created_at"],
                command=tuple(raw["command"]),
                expected_files=tuple(raw.get("expected_files", ())),
                items=tuple(BatchItem(**item) for item in raw["items"]),
            )
        except (KeyError, TypeError) as exc:
            raise ManifestError(f"invalid batch manifest: {exc}") from exc
        manifest.validate()
        return manifest

    def item(self, index: int) -> BatchItem:
        if index < 1 or index > len(self.items):
            raise ManifestError(f"array index {index} is outside 1-{len(self.items)}")
        return self.items[index - 1]


def attempt_return_path(item: BatchItem) -> Path:
    """Return the network destination dedicated to this immutable Attempt."""
    if item.return_path:
        return Path(item.return_path)
    return Path(item.case_path) / ".flowpilot" / "attempts" / item.attempt_id


def write_execution_status(path: Path, **fields: Any) -> None:
    payload = {
        "schemaVersion": 1,
        "writtenAt": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    atomic_write_json(path, payload)


def read_execution_status(path: Path, expected_attempt_id: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1:
        raise ManifestError("unsupported execution status schema")
    if payload.get("attemptId") != expected_attempt_id:
        raise ManifestError(
            f"status belongs to attempt {payload.get('attemptId')!r}, expected {expected_attempt_id!r}"
        )
    return payload
