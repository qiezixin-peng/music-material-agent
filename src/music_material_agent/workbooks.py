from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from openpyxl import load_workbook

from .util import json_default


def _json_value(value: Any) -> Any:
    try:
        return json_default(value)
    except TypeError:
        return value


def inspect_workbook_file(path: str | Path, sample_rows: int = 5) -> dict[str, Any]:
    workbook_path = Path(path).resolve()
    if not workbook_path.is_file():
        raise FileNotFoundError(workbook_path)
    workbook = load_workbook(workbook_path, read_only=True, data_only=False)
    try:
        sheets: list[dict[str, Any]] = []
        for sheet in workbook.worksheets:
            max_row = sheet.max_row or 0
            max_column = sheet.max_column or 0
            samples: list[list[Any]] = []
            if max_row and max_column:
                for row in sheet.iter_rows(
                    min_row=1,
                    max_row=min(max_row, sample_rows),
                    values_only=True,
                ):
                    samples.append([_json_value(value) for value in row])
            sheets.append(
                {
                    "name": sheet.title,
                    "max_row": max_row,
                    "max_column": max_column,
                    "sample_rows": samples,
                }
            )
        return {"path": str(workbook_path), "sheets": sheets}
    finally:
        workbook.close()


def iter_workbook_rows(
    path: str | Path,
    sheet_name: str | None,
    header_row: int,
) -> tuple[str, list[str], Iterator[tuple[int, dict[str, Any]]]]:
    if header_row < 1:
        raise ValueError("header_row must be >= 1")
    workbook_path = Path(path).resolve()
    workbook = load_workbook(workbook_path, read_only=True, data_only=False)
    sheet = workbook[sheet_name] if sheet_name else workbook.worksheets[0]
    selected_sheet = sheet.title
    header_values = next(
        sheet.iter_rows(min_row=header_row, max_row=header_row, values_only=True),
        (),
    )
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(header_values, start=1):
        base = str(value).strip() if value is not None and str(value).strip() else f"column_{index}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        headers.append(base if count == 1 else f"{base}__{count}")

    def generate() -> Iterator[tuple[int, dict[str, Any]]]:
        try:
            for row_number, values in enumerate(
                sheet.iter_rows(min_row=header_row + 1, values_only=True),
                start=header_row + 1,
            ):
                normalized = [_json_value(value) for value in values[: len(headers)]]
                if not any(value not in (None, "") for value in normalized):
                    continue
                if len(normalized) < len(headers):
                    normalized.extend([None] * (len(headers) - len(normalized)))
                yield row_number, dict(zip(headers, normalized, strict=True))
        finally:
            workbook.close()

    return selected_sheet, headers, generate()
