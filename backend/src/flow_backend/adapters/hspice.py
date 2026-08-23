from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
import re

from ..files import atomic_write_text


class SpiceRenderError(ValueError):
    pass


class MeasureParseError(ValueError):
    pass


_PARAM_START = re.compile(r"^\s*\.param(?:eter)?\b", re.IGNORECASE)
_CONTINUATION = re.compile(r"^\s*\+")
_VALUE = r"(?:'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[^\s,]+)"


def render_hspice_parameters(
    source: Path,
    destination: Path,
    values: Mapping[str, str | int | float | Decimal],
    *,
    require_all: bool = True,
    reject_duplicates: bool = True,
) -> dict[str, int]:
    """Replace scalar assignments only inside HSPICE .PARAM statement blocks.

    Matching is case-insensitive and exact by parameter name. The renderer does
    not evaluate HSPICE expressions and deliberately rejects ambiguous duplicate
    definitions by default.
    """
    text = source.read_text(encoding="utf-8")
    normalized = {name.casefold(): str(value) for name, value in values.items()}
    counts = {name: 0 for name in normalized}
    output: list[str] = []
    in_param_block = False

    patterns = {
        name: re.compile(
            rf"(?<![A-Za-z0-9_$])(?P<name>{re.escape(name)})"
            rf"(?P<equals>\s*=\s*)(?P<value>{_VALUE})",
            re.IGNORECASE,
        )
        for name in normalized
    }

    for line in text.splitlines(keepends=True):
        if _PARAM_START.match(line):
            in_param_block = True
        elif not _CONTINUATION.match(line):
            in_param_block = False

        rendered = line
        if in_param_block:
            for name, pattern in patterns.items():
                def replacement(match: re.Match[str], key: str = name) -> str:
                    counts[key] += 1
                    return f"{match.group('name')}{match.group('equals')}{normalized[key]}"

                rendered = pattern.sub(replacement, rendered)
        output.append(rendered)

    missing = [name for name, count in counts.items() if count == 0]
    duplicates = [name for name, count in counts.items() if count > 1]
    if require_all and missing:
        raise SpiceRenderError(f"parameters not found in .PARAM statements: {', '.join(missing)}")
    if reject_duplicates and duplicates:
        raise SpiceRenderError(f"parameters defined more than once: {', '.join(duplicates)}")

    atomic_write_text(destination, "".join(output))
    return counts


_ENGINEERING_SUFFIXES = {
    "t": Decimal("1e12"),
    "g": Decimal("1e9"),
    "meg": Decimal("1e6"),
    "k": Decimal("1e3"),
    "m": Decimal("1e-3"),
    "u": Decimal("1e-6"),
    "n": Decimal("1e-9"),
    "p": Decimal("1e-12"),
    "f": Decimal("1e-15"),
    "a": Decimal("1e-18"),
}
_NUMBER = re.compile(
    r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<suffix>meg|[tgkmunpfa])?(?:[A-Za-z]*)?$",
    re.IGNORECASE,
)
_FAILED_VALUES = {"failed", "fail", "error", "undefined", "nan", "not_found"}


def parse_hspice_number(token: str) -> Decimal | None:
    cleaned = token.strip()
    if cleaned.casefold() in _FAILED_VALUES:
        return None
    match = _NUMBER.fullmatch(cleaned)
    if not match:
        raise MeasureParseError(f"unsupported HSPICE numeric value: {token!r}")
    try:
        number = Decimal(match.group("number"))
    except InvalidOperation as exc:
        raise MeasureParseError(f"invalid HSPICE numeric value: {token!r}") from exc
    suffix = match.group("suffix")
    return number * _ENGINEERING_SUFFIXES.get(suffix.casefold(), Decimal(1)) if suffix else number


def _is_value(token: str) -> bool:
    try:
        parse_hspice_number(token)
        return True
    except MeasureParseError:
        return False


@dataclass(frozen=True)
class MeasureTable:
    columns: tuple[str, ...]
    rows: tuple[dict[str, Decimal | None], ...]
    metadata: tuple[str, ...] = ()


def parse_hspice_measure_table(path: Path) -> MeasureTable:
    """Parse traditional whitespace-delimited HSPICE measure data.

    The implementation accepts wrapped header/value lines used by the default
    format and the single-row MEASFORM=1 layout. CSV (MEASFORM=3) is kept as a
    separate future adapter so delimiter detection cannot silently misparse data.
    """
    metadata: list[str] = []
    data_lines: list[list[str]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("$") or line.casefold().startswith(".title"):
            metadata.append(line)
            continue
        tokens = line.split()
        if tokens:
            data_lines.append(tokens)

    if not data_lines:
        raise MeasureParseError("measure file contains no table data")

    first_value_line = next(
        (index for index, tokens in enumerate(data_lines) if all(_is_value(token) for token in tokens)),
        None,
    )
    if first_value_line is None or first_value_line == 0:
        raise MeasureParseError("could not locate measure column names followed by values")

    columns = tuple(token.casefold() for line in data_lines[:first_value_line] for token in line)
    if len(columns) != len(set(columns)):
        raise MeasureParseError("measure file contains duplicate column names")

    value_tokens = [token for line in data_lines[first_value_line:] for token in line]
    if len(value_tokens) % len(columns):
        raise MeasureParseError(
            f"measure value count {len(value_tokens)} is not divisible by column count {len(columns)}"
        )

    rows = []
    for offset in range(0, len(value_tokens), len(columns)):
        values = value_tokens[offset:offset + len(columns)]
        rows.append(dict(zip(columns, (parse_hspice_number(value) for value in values), strict=True)))
    return MeasureTable(columns=columns, rows=tuple(rows), metadata=tuple(metadata))


class HspiceMeasureParser:
    def __init__(self, initial_fields: Mapping[str, str], result_fields: Mapping[str, str]):
        self.initial_fields = {key: value.casefold() for key, value in initial_fields.items()}
        self.result_fields = {key: value.casefold() for key, value in result_fields.items()}

    def parse_initial(self, mt_path: Path) -> dict[str, Any]:
        return self._mapped_row(mt_path, self.initial_fields)

    def parse_result(self, mt_path: Path) -> dict[str, Any]:
        return self._mapped_row(mt_path, self.result_fields)

    @staticmethod
    def _mapped_row(path: Path, fields: Mapping[str, str]) -> dict[str, Any]:
        table = parse_hspice_measure_table(path)
        if len(table.rows) != 1:
            raise MeasureParseError(f"expected one measure row, found {len(table.rows)}")
        row = table.rows[0]
        missing = [source for source in fields.values() if source not in row]
        if missing:
            raise MeasureParseError(f"required measure fields are missing: {', '.join(missing)}")
        return {
            target: float(row[source]) if row[source] is not None else None
            for target, source in fields.items()
        }


class HspiceSimulator:
    def __init__(self, pvt_parameter_names: Mapping[str, str] | None = None):
        self.pvt_parameter_names = {
            key.casefold(): value for key, value in (pvt_parameter_names or {}).items()
        }

    def render_input(
        self,
        source_sp: Path,
        destination: Path,
        pvt: dict[str, Any],
        parameters: dict[str, Any],
    ) -> None:
        replacements = dict(parameters)
        for key, value in pvt.items():
            parameter_name = self.pvt_parameter_names.get(key.casefold(), key)
            replacements[parameter_name] = value
        render_hspice_parameters(source_sp, destination, replacements)

