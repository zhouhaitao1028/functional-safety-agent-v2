from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from .constants import (
    CHANGE_OVERVIEW_SHEET,
    CHANGE_RECORD_HEADER,
    COMPARE_RESULT_SHEET,
    DIFF_STATUS_HEADER,
    MANUALLY_CONFIRMED_STATUSES,
    STATUS_MODIFIED,
    STATUS_NEW_ONLY,
    STATUS_OLD_ONLY,
)


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
TOTAL_FILL = PatternFill("solid", fgColor="D9EAF7")
TOTAL_FONT = Font(bold=True)


@dataclass(frozen=True)
class ChangeOverviewStats:
    modules: int
    new_only: int
    old_only: int
    modified: int
    manually_confirmed: int

    @property
    def total_changes(self) -> int:
        return self.new_only + self.old_only + self.modified + self.manually_confirmed


def generate_change_overview(workbook_path: Path) -> ChangeOverviewStats:
    """Build or replace the module-level overview in an existing comparison workbook."""

    workbook_path = Path(workbook_path)
    if not workbook_path.exists():
        raise FileNotFoundError(f"结果文件不存在: {workbook_path}")

    try:
        workbook = load_workbook(workbook_path)
    except PermissionError as exc:
        raise PermissionError(f"无法读取结果文件，请先关闭 Excel/WPS 中打开的文件: {workbook_path}") from exc

    try:
        if COMPARE_RESULT_SHEET not in workbook.sheetnames:
            raise ValueError(f"缺少工作表: {COMPARE_RESULT_SHEET}")

        result_sheet = workbook[COMPARE_RESULT_SHEET]
        headers = _headers(result_sheet)
        module_column = _required_header(headers, "模块名字")
        status_column = _required_header(headers, DIFF_STATUS_HEADER)
        record_column = headers.get(CHANGE_RECORD_HEADER)

        summary = _collect_module_summary(
            result_sheet,
            module_column=module_column,
            status_column=status_column,
            record_column=record_column,
        )

        if CHANGE_OVERVIEW_SHEET in workbook.sheetnames:
            del workbook[CHANGE_OVERVIEW_SHEET]
        result_index = workbook.index(result_sheet)
        overview_sheet = workbook.create_sheet(CHANGE_OVERVIEW_SHEET, index=result_index)
        stats = _write_overview(overview_sheet, summary)

        try:
            workbook.save(workbook_path)
        except PermissionError as exc:
            raise PermissionError(f"无法保存结果文件，请先关闭 Excel/WPS 中打开的文件: {workbook_path}") from exc
        return stats
    finally:
        workbook.close()


def _collect_module_summary(
    sheet: Worksheet,
    *,
    module_column: int,
    status_column: int,
    record_column: int | None,
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"new_only": 0, "old_only": 0, "modified": 0, "manually_confirmed": 0}
    )
    confirmed_pairs: set[tuple[str, str]] = set()

    for row_idx in range(2, sheet.max_row + 1):
        module = _cell_text(sheet.cell(row_idx, module_column).value)
        status = _cell_text(sheet.cell(row_idx, status_column).value)
        if not module:
            continue
        if status == STATUS_NEW_ONLY:
            counts[module]["new_only"] += 1
        elif status == STATUS_OLD_ONLY:
            counts[module]["old_only"] += 1
        elif status == STATUS_MODIFIED:
            counts[module]["modified"] += 1
        elif status in MANUALLY_CONFIRMED_STATUSES:
            change_record = _cell_text(sheet.cell(row_idx, record_column).value) if record_column else ""
            pair_id = change_record or f"__row__{row_idx}"
            unique_id = (module, pair_id)
            if unique_id not in confirmed_pairs:
                confirmed_pairs.add(unique_id)
                counts[module]["manually_confirmed"] += 1

    return dict(counts)


def _write_overview(sheet: Worksheet, summary: dict[str, dict[str, int]]) -> ChangeOverviewStats:
    headers = ["模块名字", "新增", "删除", "变更", "人工确认同一接口", "总变更数"]
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(1, column, header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")

    total = {"new_only": 0, "old_only": 0, "modified": 0, "manually_confirmed": 0}
    row_idx = 2
    for module in sorted(summary, key=str.casefold):
        values = summary[module]
        module_total = sum(values.values())
        row_values = [
            module,
            values["new_only"],
            values["old_only"],
            values["modified"],
            values["manually_confirmed"],
            module_total,
        ]
        for column, value in enumerate(row_values, start=1):
            cell = sheet.cell(row_idx, column, value)
            cell.alignment = Alignment(
                horizontal="left" if column == 1 else "center",
                vertical="center",
            )
        for key in total:
            total[key] += values[key]
        row_idx += 1

    total_values = [
        "总计",
        total["new_only"],
        total["old_only"],
        total["modified"],
        total["manually_confirmed"],
        sum(total.values()),
    ]
    for column, value in enumerate(total_values, start=1):
        cell = sheet.cell(row_idx, column, value)
        cell.fill = TOTAL_FILL
        cell.font = TOTAL_FONT
        cell.alignment = Alignment(horizontal="left" if column == 1 else "center", vertical="center")

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:F{row_idx}"
    sheet.column_dimensions["A"].width = 32
    for column in ("B", "C", "D", "E", "F"):
        sheet.column_dimensions[column].width = 18
    sheet.row_dimensions[1].height = 22

    return ChangeOverviewStats(
        modules=len(summary),
        new_only=total["new_only"],
        old_only=total["old_only"],
        modified=total["modified"],
        manually_confirmed=total["manually_confirmed"],
    )


def _headers(sheet: Worksheet) -> dict[str, int]:
    return {
        _cell_text(cell.value): cell.column
        for cell in sheet[1]
        if _cell_text(cell.value)
    }


def _required_header(headers: dict[str, int], name: str) -> int:
    if name not in headers:
        raise ValueError(f"缺少列: {name}")
    return headers[name]


def _cell_text(value: Any) -> str:
    return "" if value is None else str(value).strip()
