from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .constants import (
    CHANGE_RECORD_HEADER,
    COMPARE_RESULT_SHEET,
    DIFF_STATUS_HEADER,
    EMPTY_DISPLAY,
    STATUS_MODIFIED,
    STATUS_NEW_ONLY,
    STATUS_OLD_ONLY,
    STATUS_UNCHANGED,
)
from .models import CompareConfig, CompareStats, RenameConfig
from .rename_detector import write_rename_candidates, write_rename_candidates_from_sources


RED_FONT = Font(color="FF0000")
GREEN_FONT = Font(color="00B050")
YELLOW_FILL = PatternFill("solid", fgColor="FFFF00")
NEW_ONLY_FILL = PatternFill("solid", fgColor="C6EFCE")
OLD_ONLY_FILL = PatternFill("solid", fgColor="FFC7CE")
SEPARATOR_FILL = PatternFill("solid", fgColor="DCDCDC")
SERIAL_HEADER = "序号"


class CompareError(RuntimeError):
    pass


def compare_source_workbooks(
    new_workbook_path: Path,
    new_sheet_name: str,
    old_workbook_path: Path,
    old_sheet_name: str,
    output_path: Path,
    config: CompareConfig | None = None,
    rename_config: RenameConfig | None = None,
    log_sink: Callable[[str], None] | None = None,
) -> CompareStats:
    config = config or CompareConfig()
    rename_config = rename_config or RenameConfig()
    new_workbook_path = Path(new_workbook_path)
    old_workbook_path = Path(old_workbook_path)
    output_path = Path(output_path)

    if not new_workbook_path.exists():
        raise CompareError(f"新版本工作簿不存在: {new_workbook_path}")
    if not old_workbook_path.exists():
        raise CompareError(f"旧版本工作簿不存在: {old_workbook_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    new_workbook = load_workbook(
        new_workbook_path,
        keep_vba=new_workbook_path.suffix.lower() == ".xlsm",
        data_only=True,
        read_only=False,
    )
    old_workbook = None
    output_workbook = Workbook()
    try:
        same_source = new_workbook_path.resolve() == old_workbook_path.resolve()
        old_workbook = new_workbook if same_source else load_workbook(
            old_workbook_path,
            keep_vba=old_workbook_path.suffix.lower() == ".xlsm",
            data_only=True,
            read_only=False,
        )
        if new_sheet_name not in new_workbook.sheetnames:
            raise CompareError(f"新版本工作表 {new_sheet_name} 不存在")
        if old_sheet_name not in old_workbook.sheetnames:
            raise CompareError(f"旧版本工作表 {old_sheet_name} 不存在")

        ws1 = new_workbook[new_sheet_name]
        ws2 = old_workbook[old_sheet_name]
        config, rename_config, serial_layout = _adapt_serial_layout(
            ws1,
            ws2,
            config,
            rename_config,
        )
        if serial_layout and log_sink:
            log_sink(
                "检测到新旧表A1均为“序号”：自动跳过A列，"
                f"主对比Key列={config.key_columns}，"
                f"重命名Key列={rename_config.key_columns}"
            )

        default_sheet = output_workbook.active
        output_workbook.remove(default_sheet)
        return _compare_worksheets_to_workbook(
            output_workbook=output_workbook,
            ws1=ws1,
            ws2=ws2,
            output_path=output_path,
            config=config,
            rename_config=rename_config,
        )
    finally:
        new_workbook.close()
        if old_workbook is not None and old_workbook is not new_workbook:
            old_workbook.close()
        output_workbook.close()


def compare_workbook(
    workbook_path: Path,
    output_path: Path | None = None,
    config: CompareConfig | None = None,
) -> CompareStats:
    """Run a Python implementation compatible with the original VBA compare output."""

    config = config or CompareConfig()
    workbook_path = Path(workbook_path)
    output_path = Path(output_path) if output_path else workbook_path

    if not workbook_path.exists():
        raise CompareError(f"工作簿不存在: {workbook_path}")

    workbook = load_workbook(
        workbook_path,
        keep_vba=workbook_path.suffix.lower() == ".xlsm",
        data_only=True,
    )
    try:
        if config.sheet1_name not in workbook.sheetnames:
            raise CompareError(f"工作表 {config.sheet1_name} 不存在")
        if config.sheet2_name not in workbook.sheetnames:
            raise CompareError(f"工作表 {config.sheet2_name} 不存在")

        ws1 = workbook[config.sheet1_name]
        ws2 = workbook[config.sheet2_name]
        config, _, _ = _adapt_serial_layout(
            ws1,
            ws2,
            config,
            RenameConfig(),
        )

        last_row1 = _last_row_in_column(ws1, _first_key_column(config))
        last_row2 = _last_row_in_column(ws2, _first_key_column(config))
        if last_row1 == 1:
            last_row1 = _last_row_in_column(ws1, 1)
        if last_row2 == 1:
            last_row2 = _last_row_in_column(ws2, 1)

        if last_row1 < 2:
            raise CompareError(f"{config.sheet1_name} 中没有数据")
        if last_row2 < 2:
            raise CompareError(f"{config.sheet2_name} 中没有数据")

        last_col = _last_col_in_header(ws1)
        compare_start = config.compare_col_start
        compare_end = config.compare_col_end or last_col
        if compare_start < 1 or compare_start > last_col:
            raise CompareError(f"开始列号设置无效，必须介于1和{last_col}之间")
        if compare_end < compare_start or compare_end > last_col:
            raise CompareError(f"结束列号设置无效，必须介于{compare_start}和{last_col}之间")

        diff_column = config.diff_status_column or last_col + 1
        change_record_column = config.change_record_column or diff_column + 1

        if COMPARE_RESULT_SHEET in workbook.sheetnames:
            del workbook[COMPARE_RESULT_SHEET]
        result = workbook.create_sheet(COMPARE_RESULT_SHEET)
        _write_headers(ws1, result, last_col, diff_column, change_record_column)

        new_groups = _group_rows_by_module(
            ws1,
            last_row1,
            config.module_column,
            config.included_modules,
        )
        old_groups = _group_rows_by_module(
            ws2,
            last_row2,
            config.module_column,
            config.included_modules,
        )
        old_module_indexes = {
            module: _build_key_index_for_rows(ws2, rows, config.key_columns)
            for module, rows in old_groups.items()
        }
        new_module_indexes = {
            module: _build_key_index_for_rows(ws1, rows, config.key_columns)
            for module, rows in new_groups.items()
        }

        result_row = 2
        total_diffs = 0
        sheet1_only_count = 0
        sheet2_only_count = 0
        content_diff_count = 0
        diff_cells: list[tuple[int, int, str, str]] = []
        change_records: dict[int, str] = {}

        for module, new_rows in new_groups.items():
            old_index = old_module_indexes.get(module, {})
            for source_row in new_rows:
                _copy_row_values(ws1, result, source_row, result_row, last_col)
                combined_key = generate_combined_key(ws1, source_row, config.key_columns)

                has_difference = False
                change_parts: list[str] = []

                if combined_key and combined_key in old_index:
                    match_row = old_index[combined_key]
                    for col_idx in range(compare_start, compare_end + 1):
                        val1 = _cell_text(ws1.cell(source_row, col_idx).value)
                        val2 = _cell_text(ws2.cell(match_row, col_idx).value)
                        if val1 != val2:
                            diff_cells.append((result_row, col_idx, val1, val2))
                            header_text = _cell_text(ws1.cell(1, col_idx).value) or f"列{col_idx}"
                            change_parts.append(
                                f"{header_text}：从{_display_text(val2)}改为{_display_text(val1)}"
                            )
                            has_difference = True
                            content_diff_count += 1
                else:
                    has_difference = True
                    sheet1_only_count += 1
                    change_parts.append("该模块新增接口")

                if has_difference:
                    if combined_key and combined_key in old_index:
                        result.cell(result_row, diff_column).value = STATUS_MODIFIED
                        result.cell(result_row, diff_column).font = RED_FONT
                        if change_parts:
                            change_records[result_row] = "差异列: " + "；".join(change_parts)
                    else:
                        result.cell(result_row, diff_column).value = STATUS_NEW_ONLY
                        result.cell(result_row, diff_column).font = GREEN_FONT
                        result.cell(result_row, diff_column).fill = NEW_ONLY_FILL
                        result.cell(result_row, change_record_column).value = "该模块新增接口"
                    total_diffs += 1
                else:
                    result.cell(result_row, diff_column).value = STATUS_UNCHANGED
                    result.cell(result_row, diff_column).font = GREEN_FONT

                result_row += 1

        result_row = _append_separator_row(result, result_row, change_record_column, config.sheet2_name)

        for module, old_rows in old_groups.items():
            new_index = new_module_indexes.get(module, {})
            for source_row in old_rows:
                combined_key = generate_combined_key(ws2, source_row, config.key_columns)
                if combined_key and combined_key not in new_index:
                    _copy_row_values(ws2, result, source_row, result_row, last_col)
                    result.cell(result_row, diff_column).value = STATUS_OLD_ONLY
                    result.cell(result_row, diff_column).font = RED_FONT
                    result.cell(result_row, diff_column).fill = OLD_ONLY_FILL
                    result.cell(result_row, change_record_column).value = "该模块废弃接口"
                    for col_idx in range(compare_start, compare_end + 1):
                        result.cell(result_row, col_idx).font = RED_FONT
                    sheet2_only_count += 1
                    total_diffs += 1
                    result_row += 1

        for row_idx, col_idx, original_val, compare_val in diff_cells:
            cell = result.cell(row_idx, col_idx)
            cell.fill = YELLOW_FILL
            cell_value = _cell_text(cell.value) or original_val
            append_text = f"{config.sheet2_name}：{_display_text(compare_val)}"
            new_value = f"{cell_value}\n{append_text}" if cell_value else append_text
            cell.value = _limit_excel_text(new_value, append_text)

        for row_idx, record in change_records.items():
            result.cell(row_idx, change_record_column).value = record

        _apply_vba_like_layout(result, change_record_column, diff_column)
        write_rename_candidates(workbook, result, diff_column)
        workbook.save(output_path)

        return CompareStats(
            sheet1_rows=last_row1 - 1,
            sheet2_rows=last_row2 - 1,
            key_columns=tuple(col for col in config.key_columns if col > 0),
            compare_start=compare_start,
            compare_end=compare_end,
            total_diffs=total_diffs,
            sheet1_only=sheet1_only_count,
            sheet2_only=sheet2_only_count,
            content_diff_cells=content_diff_count,
            result_sheet=COMPARE_RESULT_SHEET,
        )
    finally:
        workbook.close()


def _compare_worksheets_to_workbook(
    output_workbook: Workbook,
    ws1: Worksheet,
    ws2: Worksheet,
    output_path: Path,
    config: CompareConfig,
    rename_config: RenameConfig,
) -> CompareStats:
    last_row1 = _last_row_in_column(ws1, _first_key_column(config))
    last_row2 = _last_row_in_column(ws2, _first_key_column(config))
    if last_row1 == 1:
        last_row1 = _last_row_in_column(ws1, 1)
    if last_row2 == 1:
        last_row2 = _last_row_in_column(ws2, 1)

    if last_row1 < 2:
        raise CompareError(f"{ws1.title} 中没有数据")
    if last_row2 < 2:
        raise CompareError(f"{ws2.title} 中没有数据")

    duplicate_new_keys, duplicate_new_rows = _duplicate_key_stats(
        ws1,
        last_row1,
        config,
    )
    duplicate_old_keys, duplicate_old_rows = _duplicate_key_stats(
        ws2,
        last_row2,
        config,
    )

    last_col = _last_col_in_header(ws1)
    compare_start = config.compare_col_start
    compare_end = config.compare_col_end or last_col
    if compare_start < 1 or compare_start > last_col:
        raise CompareError(f"开始列号设置无效，必须介于1和{last_col}之间")
    if compare_end < compare_start or compare_end > last_col:
        raise CompareError(f"结束列号设置无效，必须介于{compare_start}和{last_col}之间")

    diff_column = config.diff_status_column or last_col + 1
    change_record_column = config.change_record_column or diff_column + 1

    if COMPARE_RESULT_SHEET in output_workbook.sheetnames:
        del output_workbook[COMPARE_RESULT_SHEET]
    result = output_workbook.create_sheet(COMPARE_RESULT_SHEET)
    _write_headers(ws1, result, last_col, diff_column, change_record_column)

    new_groups = _group_rows_by_module(
        ws1,
        last_row1,
        config.module_column,
        config.included_modules,
    )
    old_groups = _group_rows_by_module(
        ws2,
        last_row2,
        config.module_column,
        config.included_modules,
    )
    old_module_indexes = {
        module: _build_key_index_for_rows(ws2, rows, config.key_columns)
        for module, rows in old_groups.items()
    }
    new_module_indexes = {
        module: _build_key_index_for_rows(ws1, rows, config.key_columns)
        for module, rows in new_groups.items()
    }

    result_row = 2
    total_diffs = 0
    sheet1_only_count = 0
    sheet2_only_count = 0
    content_diff_count = 0
    diff_cells: list[tuple[int, int, str, str]] = []
    change_records: dict[int, str] = {}
    unchanged_new_rows: set[int] = set()
    unchanged_old_rows: set[int] = set()
    non_rename_modified_new_rows: set[int] = set()
    non_rename_modified_old_rows: set[int] = set()

    for module, new_rows in new_groups.items():
        old_index = old_module_indexes.get(module, {})
        for source_row in new_rows:
            _copy_row_values(ws1, result, source_row, result_row, last_col)
            combined_key = generate_combined_key(ws1, source_row, config.key_columns)

            has_difference = False
            change_parts: list[str] = []

            if combined_key and combined_key in old_index:
                match_row = old_index[combined_key]
                for col_idx in range(compare_start, compare_end + 1):
                    val1 = _cell_text(ws1.cell(source_row, col_idx).value)
                    val2 = _cell_text(ws2.cell(match_row, col_idx).value)
                    if val1 != val2:
                        diff_cells.append((result_row, col_idx, val1, val2))
                        header_text = _cell_text(ws1.cell(1, col_idx).value) or f"列{col_idx}"
                        change_parts.append(
                            f"{header_text}：从{_display_text(val2)}改为{_display_text(val1)}"
                        )
                        has_difference = True
                        content_diff_count += 1
            else:
                has_difference = True
                sheet1_only_count += 1
                change_parts.append("该模块新增接口")

            if has_difference:
                if combined_key and combined_key in old_index:
                    match_row = old_index[combined_key]
                    result.cell(result_row, diff_column).value = STATUS_MODIFIED
                    result.cell(result_row, diff_column).font = RED_FONT
                    if change_parts:
                        change_records[result_row] = "差异列: " + "；".join(change_parts)
                    if _cell_text(
                        ws1.cell(source_row, config.signal_interface_column).value
                    ) == _cell_text(
                        ws2.cell(match_row, config.signal_interface_column).value
                    ):
                        non_rename_modified_new_rows.add(source_row)
                        non_rename_modified_old_rows.add(match_row)
                else:
                    result.cell(result_row, diff_column).value = STATUS_NEW_ONLY
                    result.cell(result_row, diff_column).font = GREEN_FONT
                    result.cell(result_row, diff_column).fill = NEW_ONLY_FILL
                    result.cell(result_row, change_record_column).value = "该模块新增接口"
                total_diffs += 1
            else:
                result.cell(result_row, diff_column).value = STATUS_UNCHANGED
                result.cell(result_row, diff_column).font = GREEN_FONT
                if combined_key and combined_key in old_index:
                    unchanged_new_rows.add(source_row)
                    unchanged_old_rows.add(old_index[combined_key])

            result_row += 1

    result_row = _append_separator_row(result, result_row, change_record_column, config.sheet2_name)

    for module, old_rows in old_groups.items():
        new_index = new_module_indexes.get(module, {})
        for source_row in old_rows:
            combined_key = generate_combined_key(ws2, source_row, config.key_columns)
            if combined_key and combined_key not in new_index:
                _copy_row_values(ws2, result, source_row, result_row, last_col)
                result.cell(result_row, diff_column).value = STATUS_OLD_ONLY
                result.cell(result_row, diff_column).font = RED_FONT
                result.cell(result_row, diff_column).fill = OLD_ONLY_FILL
                result.cell(result_row, change_record_column).value = "该模块废弃接口"
                for col_idx in range(compare_start, compare_end + 1):
                    result.cell(result_row, col_idx).font = RED_FONT
                sheet2_only_count += 1
                total_diffs += 1
                result_row += 1

    for row_idx, col_idx, original_val, compare_val in diff_cells:
        cell = result.cell(row_idx, col_idx)
        cell.fill = YELLOW_FILL
        cell_value = _cell_text(cell.value) or original_val
        append_text = f"{config.sheet2_name}：{_display_text(compare_val)}"
        new_value = f"{cell_value}\n{append_text}" if cell_value else append_text
        cell.value = _limit_excel_text(new_value, append_text)

    for row_idx, record in change_records.items():
        result.cell(row_idx, change_record_column).value = record

    _apply_vba_like_layout(result, change_record_column, diff_column)
    write_rename_candidates_from_sources(
        output_workbook,
        ws1,
        ws2,
        rename_config,
        excluded_new_rows=unchanged_new_rows | non_rename_modified_new_rows,
        excluded_old_rows=unchanged_old_rows | non_rename_modified_old_rows,
    )
    output_workbook.save(output_path)

    return CompareStats(
        sheet1_rows=last_row1 - 1,
        sheet2_rows=last_row2 - 1,
        key_columns=tuple(col for col in config.key_columns if col > 0),
        compare_start=compare_start,
        compare_end=compare_end,
        total_diffs=total_diffs,
        sheet1_only=sheet1_only_count,
        sheet2_only=sheet2_only_count,
        content_diff_cells=content_diff_count,
        result_sheet=COMPARE_RESULT_SHEET,
            duplicate_new_keys=duplicate_new_keys,
            duplicate_new_rows=duplicate_new_rows,
            duplicate_old_keys=duplicate_old_keys,
            duplicate_old_rows=duplicate_old_rows,
    )


def generate_combined_key(ws: Worksheet, row_num: int, key_columns: tuple[int, ...]) -> str:
    result = ""
    valid_columns = [column for column in key_columns if column > 0]
    for index, column in enumerate(valid_columns):
        cell_value = _cell_text(ws.cell(row_num, column).value)
        if index > 0:
            result += "|"
        result += cell_value
    return result


def _first_key_column(config: CompareConfig) -> int:
    for column in config.key_columns:
        if column > 0:
            return column
    raise CompareError("请至少设置一个关键列")


def _adapt_serial_layout(
    ws1: Worksheet,
    ws2: Worksheet,
    config: CompareConfig,
    rename_config: RenameConfig,
) -> tuple[CompareConfig, RenameConfig, bool]:
    serial_layout = (
        _cell_text(ws1.cell(1, 1).value) == SERIAL_HEADER
        and _cell_text(ws2.cell(1, 1).value) == SERIAL_HEADER
    )
    if not serial_layout:
        return config, rename_config, False

    effective_config = replace(
        config,
        compare_col_start=(
            2 if config.compare_col_start == 1 else config.compare_col_start
        ),
        key_columns=(
            tuple(column + 1 for column in config.key_columns)
            if 1 in config.key_columns
            else config.key_columns
        ),
        module_column=(
            2 if config.module_column == 1 else config.module_column
        ),
        signal_interface_column=(
            9
            if config.signal_interface_column == 8
            else config.signal_interface_column
        ),
    )
    effective_rename_config = replace(
        rename_config,
        key_columns=(
            tuple(column + 1 for column in rename_config.key_columns)
            if 1 in rename_config.key_columns
            else rename_config.key_columns
        ),
    )
    return effective_config, effective_rename_config, True


def _group_rows_by_module(
    ws: Worksheet,
    last_row: int,
    module_column: int,
    included_modules: frozenset[str] | None = None,
) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for row_idx in range(2, last_row + 1):
        module_name = _cell_text(ws.cell(row_idx, module_column).value)
        if included_modules is not None and module_name not in included_modules:
            continue
        groups.setdefault(module_name, []).append(row_idx)
    return groups


def _build_key_index_for_rows(ws: Worksheet, rows: list[int], key_columns: tuple[int, ...]) -> dict[str, int]:
    index: dict[str, int] = {}
    for row_idx in rows:
        key = generate_combined_key(ws, row_idx, key_columns)
        if key:
            index[key] = row_idx
    return index


def _duplicate_key_stats(
    ws: Worksheet,
    last_row: int,
    config: CompareConfig,
) -> tuple[int, int]:
    counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row_idx in range(2, last_row + 1):
        module = _cell_text(ws.cell(row_idx, config.module_column).value)
        key = generate_combined_key(ws, row_idx, config.key_columns)
        if key:
            counts[(module, key)] += 1
    duplicate_groups = [count for count in counts.values() if count > 1]
    return len(duplicate_groups), sum(duplicate_groups)


def _write_headers(
    source: Worksheet,
    target: Worksheet,
    last_col: int,
    diff_column: int,
    change_record_column: int,
) -> None:
    for col_idx in range(1, last_col + 1):
        target.cell(1, col_idx).value = source.cell(1, col_idx).value
    target.cell(1, diff_column).value = DIFF_STATUS_HEADER
    target.cell(1, change_record_column).value = CHANGE_RECORD_HEADER


def _copy_row_values(source: Worksheet, target: Worksheet, source_row: int, target_row: int, last_col: int) -> None:
    for col_idx in range(1, last_col + 1):
        _set_cell_value(target.cell(target_row, col_idx), source.cell(source_row, col_idx).value)


def _append_separator_row(target: Worksheet, row_idx: int, change_record_column: int, sheet2_name: str) -> int:
    _set_cell_value(target.cell(row_idx, 1), f"======= 以下为{sheet2_name}独有的数据 =======")
    for col_idx in range(1, change_record_column + 1):
        cell = target.cell(row_idx, col_idx)
        cell.fill = SEPARATOR_FILL
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for col_idx in range(2, change_record_column + 1):
        target.cell(row_idx, col_idx).value = None
    return row_idx + 1


def _apply_vba_like_layout(result: Worksheet, change_record_column: int, diff_column: int) -> None:
    for col_idx in range(1, change_record_column + 1):
        column_letter = get_column_letter(col_idx)
        result.column_dimensions[column_letter].width = 20
        for cell in result[column_letter]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    for column_letter, width in {"A": 15, "D": 15, "B": 7, "J": 7, "C": 5, "G": 5, "L": 5}.items():
        result.column_dimensions[column_letter].width = width

    last_data_row = _last_row_in_column(result, 1)
    for col_idx in range(1, 15):
        if _is_blank_column(result, col_idx, last_data_row):
            result.column_dimensions[get_column_letter(col_idx)].hidden = True

    for col_idx in range(15, 27):
        result.column_dimensions[get_column_letter(col_idx)].hidden = True

    for col_idx in (diff_column, change_record_column):
        column_letter = get_column_letter(col_idx)
        for cell in result[column_letter]:
            cell.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)


def _is_blank_column(ws: Worksheet, col_idx: int, last_row: int) -> bool:
    for row_idx in range(2, last_row + 1):
        if _cell_text(ws.cell(row_idx, col_idx).value).strip():
            return False
    return True


def _last_row_in_column(ws: Worksheet, col_idx: int) -> int:
    for row_idx in range(ws.max_row, 0, -1):
        if _cell_text(ws.cell(row_idx, col_idx).value) != "":
            return row_idx
    return 1


def _last_col_in_header(ws: Worksheet) -> int:
    for col_idx in range(ws.max_column, 0, -1):
        if _cell_text(ws.cell(1, col_idx).value) != "":
            return col_idx
    return 1


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _set_cell_value(cell, value: Any) -> None:
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"


def _display_text(value: str) -> str:
    return EMPTY_DISPLAY if value == "" else value


def _limit_excel_text(value: str, append_text: str) -> str:
    if len(value) <= 32767:
        return value
    suffix = "\n" + append_text
    max_original = 32767 - len(suffix) - 3
    if max_original > 0:
        return value[:max_original] + "..." + suffix
    return "[内容过长]" + suffix
