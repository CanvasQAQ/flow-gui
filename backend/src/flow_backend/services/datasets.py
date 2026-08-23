from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import json
import re
import shutil
import uuid

from ..db import connect, transaction
from ..files import sha256_files
from ..ports import MeasureParser


class DatasetScanError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetConvention:
    sp_pattern: str = r"^(?P<stem>.+)\.sp(?P<corner>[^/]*)$"
    mt_template: str = "{stem}.mt{corner}"
    recursive: bool = False

    def compile(self) -> re.Pattern[str]:
        pattern = re.compile(self.sp_pattern)
        if not {"stem", "corner"} <= set(pattern.groupindex):
            raise DatasetScanError("sp_pattern must define named groups 'stem' and 'corner'")
        return pattern


@dataclass(frozen=True)
class DiscoveredCorner:
    number: int
    key: str
    sp_path: Path
    mt_path: Path


def discover_corners(source_path: Path, convention: DatasetConvention) -> list[DiscoveredCorner]:
    if not source_path.is_dir():
        raise DatasetScanError(f"dataset directory does not exist: {source_path}")
    matcher = convention.compile()
    paths = source_path.rglob("*") if convention.recursive else source_path.iterdir()
    matches: list[tuple[str, str, Path, Path]] = []
    for path in paths:
        if not path.is_file():
            continue
        relative = path.relative_to(source_path).as_posix()
        match = matcher.fullmatch(relative)
        if not match:
            continue
        parts = match.groupdict()
        mt_relative = convention.mt_template.format(**parts)
        key = parts["corner"] or parts["stem"]
        matches.append((key, parts["corner"], path, source_path / mt_relative))

    matches.sort(key=lambda item: _natural_key(item[0]))
    used_numbers: set[int] = set()
    discovered: list[DiscoveredCorner] = []
    next_number = 1
    for key, corner_suffix, sp_path, mt_path in matches:
        digits = re.fullmatch(r"\D*(\d+)", corner_suffix)
        candidate = int(digits.group(1)) if digits else None
        if candidate is None or candidate in used_numbers:
            while next_number in used_numbers:
                next_number += 1
            candidate = next_number
        used_numbers.add(candidate)
        discovered.append(DiscoveredCorner(candidate, key, sp_path, mt_path))
    return discovered


def _natural_key(value: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value))


class DatasetService:
    def __init__(self, database_path: Path, snapshot_root: Path, parser: MeasureParser):
        self.database_path = database_path
        self.snapshot_root = snapshot_root
        self.parser = parser

    def create(
        self, name: str, source_path: Path, convention: DatasetConvention,
        dataset_id: str | None = None,
    ) -> str:
        dataset_id = dataset_id or f"ds_{uuid.uuid4().hex}"
        with transaction(self.database_path) as connection:
            existing = connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
            if existing is not None:
                if existing["name"] == name and existing["source_path"] == str(source_path.resolve()):
                    return dataset_id
                raise DatasetScanError("deterministic Dataset ID already belongs to another request")
            connection.execute(
                "INSERT INTO datasets(id, name, source_path, scan_config_json) VALUES (?, ?, ?, ?)",
                (dataset_id, name, str(source_path.resolve()), json.dumps(asdict(convention))),
            )
        return dataset_id

    def scan(
        self, dataset_id: str, mode: Literal["append", "overwrite"] = "append",
        scan_id: str | None = None,
    ) -> dict:
        with connect(self.database_path) as connection:
            dataset = connection.execute(
                "SELECT source_path, scan_config_json FROM datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        if dataset is None:
            raise DatasetScanError(f"unknown dataset: {dataset_id}")
        convention = DatasetConvention(**json.loads(dataset["scan_config_json"]))
        discovered = discover_corners(Path(dataset["source_path"]), convention)
        scan_id = scan_id or f"scan_{uuid.uuid4().hex}"
        with connect(self.database_path) as connection:
            previous_scan = connection.execute(
                "SELECT mode, status, summary_json FROM dataset_scans WHERE id = ?", (scan_id,)
            ).fetchone()
        if previous_scan is not None:
            if previous_scan["mode"] != mode:
                raise DatasetScanError("scan request ID was reused with a different mode")
            if previous_scan["status"] == "complete":
                return {"scanId": scan_id, "mode": mode, **json.loads(previous_scan["summary_json"]), "replayed": True}
        summary = {"new": 0, "newVersions": 0, "recovered": 0, "invalid": 0, "missing": 0, "unchanged": 0}
        seen_keys = {item.key for item in discovered}

        with transaction(self.database_path) as connection:
            connection.execute(
                "INSERT INTO dataset_scans(id, dataset_id, mode, status) VALUES (?, ?, ?, 'running')",
                (scan_id, dataset_id, mode),
            )
            existing_rows = connection.execute(
                "SELECT * FROM corners WHERE dataset_id = ?", (dataset_id,)
            ).fetchall()
            existing = {row["corner_key"]: row for row in existing_rows}

            for item in discovered:
                previous = existing.get(item.key)
                if mode == "append" and previous is not None and previous["availability"] == "available":
                    summary["unchanged"] += 1
                    continue
                self._scan_one(connection, dataset_id, item, previous, summary)

            if mode == "overwrite":
                for key, previous in existing.items():
                    if key not in seen_keys:
                        connection.execute(
                            "UPDATE corners SET availability = 'missing', error_json = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (json.dumps({"code": "sp_missing", "message": "SP file was not found during overwrite scan"}), previous["id"]),
                        )
                        summary["missing"] += 1

            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE datasets SET scan_status = 'complete', scanned_at = ?, updated_at = ? WHERE id = ?",
                (now, now, dataset_id),
            )
            connection.execute(
                "UPDATE dataset_scans SET status = 'complete', summary_json = ?, finished_at = ? WHERE id = ?",
                (json.dumps(summary), now, scan_id),
            )
        return {"scanId": scan_id, "mode": mode, **summary}

    def _scan_one(self, connection, dataset_id: str, item: DiscoveredCorner, previous, summary: dict) -> None:
        corner_id = previous["id"] if previous is not None else f"corner_{uuid.uuid4().hex}"
        if previous is None:
            connection.execute(
                "INSERT INTO corners(id, dataset_id, corner_number, corner_key, availability) VALUES (?, ?, ?, ?, 'invalid')",
                (corner_id, dataset_id, item.number, item.key),
            )
            summary["new"] += 1

        if not item.mt_path.is_file():
            self._mark_invalid(connection, corner_id, "mt_missing", f"MT file not found: {item.mt_path}")
            summary["invalid"] += 1
            return

        try:
            initial_code = self.parser.parse_initial(item.mt_path)
            reference_metrics = self.parser.parse_result(item.mt_path)
            fingerprint = sha256_files(item.sp_path, item.mt_path)
            current = connection.execute(
                "SELECT id, content_fingerprint FROM corner_input_versions WHERE corner_id = ? ORDER BY version_number DESC LIMIT 1",
                (corner_id,),
            ).fetchone()
            if current is not None and current["content_fingerprint"] == fingerprint:
                version_id = current["id"]
                summary["unchanged"] += 1
            else:
                version_number = connection.execute(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM corner_input_versions WHERE corner_id = ?",
                    (corner_id,),
                ).fetchone()[0]
                version_id = f"input_{uuid.uuid4().hex}"
                target = self.snapshot_root / dataset_id / item.key / fingerprint
                target.mkdir(parents=True, exist_ok=True)
                sp_snapshot = target / "source.sp"
                mt_snapshot = target / "initial.mt"
                if not sp_snapshot.exists():
                    shutil.copy2(item.sp_path, sp_snapshot)
                    shutil.copy2(item.mt_path, mt_snapshot)
                connection.execute(
                    """
                    INSERT INTO corner_input_versions(
                        id, corner_id, version_number, sp_snapshot_path, mt_snapshot_path,
                        content_fingerprint, initial_code_json, reference_metrics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id, corner_id, version_number, str(sp_snapshot), str(mt_snapshot),
                        fingerprint, json.dumps(initial_code), json.dumps(reference_metrics),
                    ),
                )
                summary["newVersions"] += 1
            was_unavailable = previous is not None and previous["availability"] != "available"
            connection.execute(
                """
                UPDATE corners SET availability = 'available', current_input_version_id = ?,
                    error_json = NULL, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (version_id, corner_id),
            )
            if was_unavailable:
                summary["recovered"] += 1
        except Exception as exc:
            self._mark_invalid(connection, corner_id, "parse_failed", str(exc))
            summary["invalid"] += 1

    @staticmethod
    def _mark_invalid(connection, corner_id: str, code: str, message: str) -> None:
        connection.execute(
            "UPDATE corners SET availability = 'invalid', error_json = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps({"code": code, "message": message}), corner_id),
        )
