from __future__ import annotations

import re


_SPREADSHEET_ROW_RE = re.compile(r"\s*\[ROW\s+\d+\]\s*(.*)")
_SPREADSHEET_CELL_RE = re.compile(r"(?:^|\t)\s*[A-Z]{1,2}\d+=")


def _spreadsheet_cells(row: str) -> tuple[str, ...]:
    """Return the filled cells of a spreadsheet row, without their addresses.

    Several organizer workbooks are extracted as "[ROW 4] B4=... C4=...", and
    the address scaffolding reached answers verbatim — a quoted bullet read
    "[ROW 4] B4=새로 시행되는 실물이전 제도에 따라". The addresses identify
    nothing a reader can use and most cells in these sheets are empty, so the
    filled values are kept and the scaffolding is dropped.
    """

    parts = _SPREADSHEET_CELL_RE.split(row)
    return tuple(value for part in parts if (value := part.strip()))


def normalize_evidence_tables(excerpt: str) -> str:
    """Attach explicit table headers to cells, preserving surrounding prose.

    Only complete, equally sized tab-delimited rows from the same numbered
    table are joined. Missing headers or merged cells are not inferred.
    Generation recovery and rule verification use this identical rendering.
    """

    headers: dict[str, tuple[str, ...]] = {}
    pending: tuple[str, int] | None = None
    lines: list[str] = []
    for line in excerpt.splitlines():
        spreadsheet = _SPREADSHEET_ROW_RE.match(line)
        if spreadsheet is not None:
            cells = _spreadsheet_cells(spreadsheet.group(1))
            if cells:
                lines.append("; ".join(cells))
            continue
        marker = re.fullmatch(r"\s*\[TABLE\s+(\d+)\s+ROW\s+(\d+)\]\s*", line)
        if marker is not None:
            pending = (marker.group(1), int(marker.group(2)))
            continue
        if pending is None:
            # Delimited cells without their header cannot become prose claims.
            if "\t" not in line.strip() and line.count("|") < 2:
                lines.append(line)
            continue
        if not line.strip():
            continue
        table_id, row_number = pending
        pending = None
        cells = tuple(cell.strip() for cell in line.split("\t"))
        if row_number == 1:
            headers.pop(table_id, None)
            if len(cells) >= 2 and all(cells):
                headers[table_id] = cells
            continue
        header = headers.get(table_id)
        if header is None or len(header) != len(cells) or not all(cells):
            continue
        lines.append("; ".join(f"{label}: {cell}" for label, cell in zip(header, cells)))
    return "\n".join(lines)
