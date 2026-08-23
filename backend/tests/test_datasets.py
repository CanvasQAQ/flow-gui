from pathlib import Path

from flow_backend.adapters.hspice import HspiceMeasureParser
from flow_backend.db import connect, migrate
from flow_backend.services.datasets import DatasetConvention, DatasetService, discover_corners


def parser() -> HspiceMeasureParser:
    return HspiceMeasureParser(
        initial_fields={"rangesel": "rangesel", "vrefsel_0": "vref0"},
        result_fields={"loss_0": "loss0"},
    )


def write_corner(root: Path, number: int, code: int = 1) -> None:
    (root / f"base.sp{number}").write_text(
        f"title\n.param rangeselcode={code}\n.end\n", encoding="utf-8"
    )
    (root / f"base.mt{number}").write_text(
        f"rangesel vref0 loss0\n{code} 2 1.5e-3\n", encoding="utf-8"
    )


def test_discover_and_scan_with_immutable_versions(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_corner(source, 1)
    write_corner(source, 2)
    database = tmp_path / "db.sqlite3"
    migrate(database)
    service = DatasetService(database, tmp_path / "snapshots", parser())
    dataset_id = service.create("Test", source, DatasetConvention())

    first = service.scan(dataset_id, "append")
    assert first["new"] == 2
    assert first["newVersions"] == 2
    assert [item.number for item in discover_corners(source, DatasetConvention())] == [1, 2]

    write_corner(source, 1, code=9)
    append = service.scan(dataset_id, "append")
    assert append["unchanged"] == 2
    overwrite = service.scan(dataset_id, "overwrite")
    assert overwrite["newVersions"] == 1

    with connect(database) as connection:
        versions = connection.execute(
            "SELECT version_number, initial_code_json FROM corner_input_versions "
            "JOIN corners ON corners.id = corner_input_versions.corner_id "
            "WHERE corners.corner_key = '1' ORDER BY version_number"
        ).fetchall()
    assert len(versions) == 2
    assert '"rangesel": 9.0' in versions[1]["initial_code_json"]


def test_missing_mt_marks_only_that_corner_invalid(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_corner(source, 1)
    (source / "base.sp2").write_text("title\n.end\n", encoding="utf-8")
    database = tmp_path / "db.sqlite3"
    migrate(database)
    service = DatasetService(database, tmp_path / "snapshots", parser())
    dataset_id = service.create("Test", source, DatasetConvention())

    result = service.scan(dataset_id)
    assert result["new"] == 2
    assert result["invalid"] == 1
    with connect(database) as connection:
        states = dict(connection.execute("SELECT corner_key, availability FROM corners"))
    assert states == {"1": "available", "2": "invalid"}

