from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook


def list_sheet_names(workbook_path: Path) -> list[str]:
    workbook = load_workbook(
        workbook_path,
        read_only=True,
        data_only=False,
        keep_vba=workbook_path.suffix.lower() == ".xlsm",
    )
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def pick_default_sheet(sheet_names: list[str]) -> str:
    if not sheet_names:
        return ""
    if "Sheet1" in sheet_names:
        return "Sheet1"
    return sheet_names[0]


def list_module_names(workbook_path: Path, sheet_name: str) -> list[str]:
    workbook = load_workbook(
        workbook_path,
        read_only=True,
        data_only=True,
        keep_vba=workbook_path.suffix.lower() == ".xlsm",
    )
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"{workbook_path.name} 中不存在 Sheet: {sheet_name}")
        sheet = workbook[sheet_name]
        modules: set[str] = set()
        for row in sheet.iter_rows(min_row=2, min_col=1, max_col=1, values_only=True):
            value = row[0]
            module = "" if value is None else str(value).strip()
            if module:
                modules.add(module)
        return sorted(modules)
    finally:
        workbook.close()
