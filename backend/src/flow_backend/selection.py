def parse_number_ranges(expression: str) -> tuple[int, ...]:
    """Parse a comma-separated list such as `1,2,5-8`."""
    values: set[int] = set()
    if not expression.strip():
        return ()
    for raw_part in expression.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("empty Corner selection segment")
        if "-" in part:
            pieces = part.split("-")
            if len(pieces) != 2 or not all(piece.strip().isdigit() for piece in pieces):
                raise ValueError(f"invalid Corner range: {part!r}")
            start, end = (int(piece) for piece in pieces)
            if start < 1 or end < start:
                raise ValueError(f"invalid Corner range: {part!r}")
            values.update(range(start, end + 1))
        elif part.isdigit() and int(part) >= 1:
            values.add(int(part))
        else:
            raise ValueError(f"invalid Corner number: {part!r}")
    return tuple(sorted(values))


def resolve_number_selection(
    universe: set[int], include: tuple[int, ...] = (), exclude: tuple[int, ...] = ()
) -> tuple[int, ...]:
    selected = set(include) if include else set(universe)
    return tuple(sorted((selected & universe) - set(exclude)))

