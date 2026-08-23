from decimal import Decimal
from pathlib import Path

import pytest

from flow_backend.adapters.hspice import (
    HspiceMeasureParser,
    SpiceRenderError,
    parse_hspice_measure_table,
    parse_hspice_number,
    render_hspice_parameters,
)


def test_render_param_assignments_without_touching_comments_or_similar_names(tmp_path: Path) -> None:
    source = tmp_path / "source.sp"
    destination = tmp_path / "case" / "testcase.sp"
    source.write_text(
        "Example\n"
        "* .param rangeselcode = 999\n"
        ".PARAM rangeselcode = 1 othercode=2\n"
        "+ vrefsel0code='3' rangeselcode_backup=8\n"
        "R1 out 0 rangeselcode\n"
        ".end\n",
        encoding="utf-8",
    )

    counts = render_hspice_parameters(
        source,
        destination,
        {"rangeselcode": 12, "vrefsel0code": 7},
    )

    assert counts == {"rangeselcode": 1, "vrefsel0code": 1}
    rendered = destination.read_text(encoding="utf-8")
    assert ".PARAM rangeselcode = 12 othercode=2" in rendered
    assert "+ vrefsel0code=7 rangeselcode_backup=8" in rendered
    assert "* .param rangeselcode = 999" in rendered


def test_render_rejects_missing_and_duplicate_parameters(tmp_path: Path) -> None:
    source = tmp_path / "source.sp"
    source.write_text("title\n.param xcode=1\n.param xcode=2\n.end\n", encoding="utf-8")
    with pytest.raises(SpiceRenderError, match="more than once"):
        render_hspice_parameters(source, tmp_path / "out.sp", {"xcode": 3})
    with pytest.raises(SpiceRenderError, match="not found"):
        render_hspice_parameters(source, tmp_path / "out.sp", {"missing": 3})


def test_parse_hspice_numbers() -> None:
    assert parse_hspice_number("1.2e-3") == Decimal("1.2e-3")
    assert parse_hspice_number("2.5n") == Decimal("2.5e-9")
    assert parse_hspice_number("3meg") == Decimal("3e6")
    assert parse_hspice_number("failed") is None


def test_parse_wrapped_measure_table_and_mapping(tmp_path: Path) -> None:
    mt = tmp_path / "case.mt0"
    mt.write_text(
        "$DATA1 SOURCE='HSPICE' VERSION='reference'\n"
        ".TITLE sample\n"
        "rangesel vref0 vref90\n"
        "loss_0 loss_90 alter#\n"
        "12 2 3\n"
        "1.25e-3 failed 1\n",
        encoding="utf-8",
    )
    table = parse_hspice_measure_table(mt)
    assert table.columns == ("rangesel", "vref0", "vref90", "loss_0", "loss_90", "alter#")
    assert table.rows[0]["rangesel"] == Decimal(12)
    assert table.rows[0]["loss_90"] is None

    parser = HspiceMeasureParser(
        initial_fields={"rangesel": "rangesel", "vrefsel_0": "vref0"},
        result_fields={"loss_0": "loss_0", "loss_90": "loss_90"},
    )
    assert parser.parse_initial(mt) == {"rangesel": 12.0, "vrefsel_0": 2.0}
    assert parser.parse_result(mt) == {"loss_0": 0.00125, "loss_90": None}

