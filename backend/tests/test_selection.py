import pytest

from flow_backend.selection import parse_number_ranges, resolve_number_selection


def test_corner_number_selection() -> None:
    assert parse_number_ranges("1, 3-5, 5") == (1, 3, 4, 5)
    assert resolve_number_selection(set(range(1, 7)), (1, 2, 3, 4), (2, 4)) == (1, 3)
    assert resolve_number_selection(set(range(1, 5)), exclude=(2, 3)) == (1, 4)
    with pytest.raises(ValueError):
        parse_number_ranges("5-2")

