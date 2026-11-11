from __future__ import annotations

from dataclasses import dataclass

from .constants import DEFAULT_NEW_SHEET, DEFAULT_OLD_SHEET


@dataclass(frozen=True)
class CompareConfig:
    sheet1_name: str = DEFAULT_NEW_SHEET
    sheet2_name: str = DEFAULT_OLD_SHEET
    compare_col_start: int = 1
    compare_col_end: int = 0
    key_columns: tuple[int, ...] = (1, 2, 3, 5, 6, 7, 8, 14)
    module_column: int = 1
    signal_interface_column: int = 8
    diff_status_column: int = 0
    change_record_column: int = 0
    included_modules: frozenset[str] | None = None


@dataclass(frozen=True)
class RenameConfig:
    key_columns: tuple[int, ...] = (1, 2, 3, 5, 6, 7, 9)
    key_threshold: float = 1.00
    signal_threshold: float = 0.40
    max_candidates_per_row: int = 10
    ngram_size: int = 3
    max_ngram_bucket: int = 2000
    included_modules: frozenset[str] | None = None


@dataclass(frozen=True)
class CompareStats:
    sheet1_rows: int
    sheet2_rows: int
    key_columns: tuple[int, ...]
    compare_start: int
    compare_end: int
    total_diffs: int
    sheet1_only: int
    sheet2_only: int
    content_diff_cells: int
    result_sheet: str
    duplicate_new_keys: int = 0
    duplicate_new_rows: int = 0
    duplicate_old_keys: int = 0
    duplicate_old_rows: int = 0
