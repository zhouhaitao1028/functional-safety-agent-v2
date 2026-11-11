from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from .constants import RENAME_CANDIDATE_SHEET, STATUS_NEW_ONLY, STATUS_OLD_ONLY
from .models import RenameConfig


MODULE_COLUMN = 1
SIGNAL_DIRECTION_COLUMN = 2
PARAM_METHOD_COLUMN = 3
SIGNAL_UPSTREAM_COLUMN = 4
PP_TOPIC_COLUMN = 5
DATA_TYPE_LEVEL1_COLUMN = 6
DATA_TYPE_LEVEL2_COLUMN = 7
SIGNAL_COLUMN = 8
MEANING_COLUMN = 9
DATA_TYPE_COLUMN = 10
MAPPING_COLUMN = 14
DIRECT_OUTPUT_SOURCE_COLUMNS = tuple(range(1, 16))
DATA_TYPE_COLUMNS = (6, 7, 12, 13)
CONTEXT_COLUMNS = (2, 3, 5)
HARD_CONSTRAINT_COLUMNS = (1, 2, 3)
STRICT_RENAME_KEY_COLUMNS = (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15)
MIN_FUZZY_SCORE = 55
CORE_HIGH_CONFIDENCE = 0.85
CORE_LCS_MIN_SCORE = 0.50

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STRICT_FILL = PatternFill("solid", fgColor="C6EFCE")
FUZZY_FILL = PatternFill("solid", fgColor="FFF2CC")
DIFF_FILL = PatternFill("solid", fgColor="FFFF00")
OLD_FONT = Font(color="C00000")
NEW_FONT = Font(color="00A000")

WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[\u4e00-\u9fff]+")
SPLIT_RE = re.compile(r"[_\.\-/:\s]+")

NOISE_TOKENS = {
    "vms",
    "mcu",
    "mcu1",
    "awap",
    "hmim",
    "dmff",
    "ipd",
    "ripd",
    "ipdbp",
    "bp",
    "plt",
    "acc",
    "had",
    "apasc",
    "acas",
    "adas",
    "body",
    "hmi",
    "bkb",
    "autolight",
}
TYPE_SUFFIX_TOKENS = {
    "u8",
    "u16",
    "u32",
    "w16",
    "b",
    "bool",
    "single",
    "float",
    "double",
    "s8",
    "s16",
    "s32",
}
WEAK_SUFFIX_TOKENS = {
    "rddy",
    "ready",
    "raw",
    "filtered",
    "value",
    "val",
}


@dataclass(frozen=True)
class ResultRow:
    row_idx: int
    module: str
    signal: str
    strict_key: str


@dataclass(frozen=True)
class RenameCandidate:
    level: str
    score: int
    module: str
    old_signal: str
    new_signal: str
    old_result_row: int
    new_result_row: int
    match_key: str
    reasons: str
    risks: str


@dataclass(frozen=True)
class SourceRow:
    row_idx: int
    module: str
    direction: str
    param_method: str
    upstream: str
    pp_topic: str
    data_type_level1: str
    data_type_level2: str
    data_type: str
    signal: str
    key_text: str
    core_text: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class DirectRenameCandidate:
    score: int
    key_similarity: float
    signal_similarity: float
    old_row: SourceRow
    new_row: SourceRow
    reasons: str
    risks: str


def write_rename_candidates_from_sources(
    workbook,
    new_sheet: Worksheet,
    old_sheet: Worksheet,
    config: RenameConfig | None = None,
    excluded_new_rows: set[int] | None = None,
    excluded_old_rows: set[int] | None = None,
) -> int:
    if RENAME_CANDIDATE_SHEET in workbook.sheetnames:
        del workbook[RENAME_CANDIDATE_SHEET]
    sheet = workbook.create_sheet(RENAME_CANDIDATE_SHEET)
    candidates = find_rename_candidates_from_sources(
        new_sheet,
        old_sheet,
        config or RenameConfig(),
        excluded_new_rows=excluded_new_rows,
        excluded_old_rows=excluded_old_rows,
    )
    _write_direct_sheet(sheet, candidates, _direct_source_headers(new_sheet))
    return len(candidates)


def find_rename_candidates_from_sources(
    new_sheet: Worksheet,
    old_sheet: Worksheet,
    config: RenameConfig,
    excluded_new_rows: set[int] | None = None,
    excluded_old_rows: set[int] | None = None,
) -> list[DirectRenameCandidate]:
    new_rows = _collect_source_rows(
        new_sheet,
        config.key_columns,
        config.included_modules,
        excluded_row_indexes=excluded_new_rows,
    )
    old_rows = _collect_source_rows(
        old_sheet,
        config.key_columns,
        config.included_modules,
        excluded_row_indexes=excluded_old_rows,
    )
    new_presence = _build_signal_presence(new_rows)
    old_presence = _build_signal_presence(old_rows)
    old_index = _build_ngram_index(old_rows, config)
    all_candidates: list[DirectRenameCandidate] = []

    for new_row in new_rows:
        candidate_ids = _recall_old_candidates(new_row, old_rows, old_index, config)
        for old_id in candidate_ids:
            old_row = old_rows[old_id]
            if _is_coexisting_signal_pair(new_row, old_row, new_presence, old_presence):
                continue
            candidate = _score_source_pair(new_row, old_row, config)
            if candidate is not None:
                all_candidates.append(candidate)

    all_candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    selected: list[DirectRenameCandidate] = []
    used_new: set[int] = set()
    used_old: set[int] = set()
    for candidate in all_candidates:
        if candidate.new_row.row_idx in used_new or candidate.old_row.row_idx in used_old:
            continue
        used_new.add(candidate.new_row.row_idx)
        used_old.add(candidate.old_row.row_idx)
        selected.append(candidate)
    return _remove_same_module_upstream_matches(_remove_cross_matches(selected))


def write_rename_candidates(workbook, result_sheet: Worksheet, diff_status_column: int) -> int:
    if RENAME_CANDIDATE_SHEET in workbook.sheetnames:
        del workbook[RENAME_CANDIDATE_SHEET]
    sheet = workbook.create_sheet(RENAME_CANDIDATE_SHEET)
    candidates = find_rename_candidates(result_sheet, diff_status_column)
    _write_sheet(sheet, candidates)
    return len(candidates)


def find_rename_candidates(result_sheet: Worksheet, diff_status_column: int) -> list[RenameCandidate]:
    new_pool, old_pool = _collect_pools(result_sheet, diff_status_column)
    all_candidates: list[tuple[str, str, RenameCandidate]] = []

    for module, new_rows in new_pool.items():
        old_rows = old_pool.get(module, [])
        for new_row in new_rows:
            for old_row in old_rows:
                candidate = _score_pair(result_sheet, new_row, old_row)
                if candidate is not None:
                    all_candidates.append((_identity(new_row), _identity(old_row), candidate))

    all_candidates.sort(key=lambda item: item[2].score, reverse=True)
    used_new: set[str] = set()
    used_old: set[str] = set()
    selected: list[RenameCandidate] = []
    for new_id, old_id, candidate in all_candidates:
        if new_id in used_new or old_id in used_old:
            continue
        used_new.add(new_id)
        used_old.add(old_id)
        selected.append(candidate)
    return selected


def _collect_pools(
    result_sheet: Worksheet,
    diff_status_column: int,
) -> tuple[dict[str, list[ResultRow]], dict[str, list[ResultRow]]]:
    new_pool: dict[str, list[ResultRow]] = {}
    old_pool: dict[str, list[ResultRow]] = {}
    for row_idx in range(2, result_sheet.max_row + 1):
        status = _cell_text(result_sheet.cell(row_idx, diff_status_column).value)
        if status not in {STATUS_NEW_ONLY, STATUS_OLD_ONLY}:
            continue
        module = _cell_text(result_sheet.cell(row_idx, MODULE_COLUMN).value)
        row = ResultRow(
            row_idx=row_idx,
            module=module,
            signal=_cell_text(result_sheet.cell(row_idx, SIGNAL_COLUMN).value),
            strict_key=_combined_key(result_sheet, row_idx, STRICT_RENAME_KEY_COLUMNS),
        )
        pool = new_pool if status == STATUS_NEW_ONLY else old_pool
        pool.setdefault(module, []).append(row)
    return new_pool, old_pool


def _score_pair(sheet: Worksheet, new_row: ResultRow, old_row: ResultRow) -> RenameCandidate | None:
    if new_row.module != old_row.module:
        return None

    hard_constraint_score = _columns_equal_ratio(sheet, new_row.row_idx, old_row.row_idx, HARD_CONSTRAINT_COLUMNS)
    if hard_constraint_score < 1:
        return None

    datatype_score = _columns_equal_ratio(sheet, new_row.row_idx, old_row.row_idx, DATA_TYPE_COLUMNS)
    if datatype_score < 1:
        return None

    if new_row.strict_key and new_row.strict_key == old_row.strict_key:
        return RenameCandidate(
            level="强匹配",
            score=100,
            module=new_row.module,
            old_signal=old_row.signal,
            new_signal=new_row.signal,
            old_result_row=old_row.row_idx,
            new_result_row=new_row.row_idx,
            match_key=new_row.strict_key,
            reasons="除H列外，1,2,3,4,5,6,7,9,10,11,12,13,14,15列完全一致",
            risks="",
        )

    core_old = _signal_core(old_row.signal)
    core_new = _signal_core(new_row.signal)
    core_score = _ordered_token_overlap(core_new, core_old)
    core_lcs_score = _lcs_similarity(_format_core(core_new), _format_core(core_old))
    signal_score = _name_similarity(new_row.signal, old_row.signal)
    meaning_score = _text_similarity(
        _cell_text(sheet.cell(new_row.row_idx, MEANING_COLUMN).value),
        _cell_text(sheet.cell(old_row.row_idx, MEANING_COLUMN).value),
    )
    mapping_score = _text_similarity(
        _cell_text(sheet.cell(new_row.row_idx, MAPPING_COLUMN).value),
        _cell_text(sheet.cell(old_row.row_idx, MAPPING_COLUMN).value),
    )
    context_score = _columns_equal_ratio(sheet, new_row.row_idx, old_row.row_idx, CONTEXT_COLUMNS)

    if core_lcs_score < CORE_LCS_MIN_SCORE:
        return None

    score = round(
        core_lcs_score * 45
        + core_score * 20
        + signal_score * 10
        + meaning_score * 10
        + mapping_score * 10
        + context_score * 5
        + datatype_score * 10
    )
    if max(core_score, core_lcs_score) >= CORE_HIGH_CONFIDENCE and datatype_score > 0:
        score = max(score, 75)
    if score < MIN_FUZZY_SCORE:
        return None

    reasons = [
        f"Signal核心LCS相似({core_lcs_score:.2f}: {_format_core(core_old)} -> {_format_core(core_new)})",
        f"Signal核心名相似({core_score:.2f}: {_format_core(core_old)} -> {_format_core(core_new)})",
        f"Signal原始名相似({signal_score:.2f})",
        f"中文含义相似({meaning_score:.2f})",
        f"信号Mapping相似({mapping_score:.2f})",
        f"1/2/3列硬约束一致({hard_constraint_score:.2f})",
        f"上下文一致度({context_score:.2f})",
        f"数据类型一致度({datatype_score:.2f})",
    ]
    risks: list[str] = []
    if core_lcs_score < 0.65:
        risks.append("Signal核心LCS相似度偏低")
    if core_score < 0.45:
        risks.append("Signal核心token相似度偏低")
    if signal_score < 0.45:
        risks.append("Signal原始名相似度偏低")
    if meaning_score < 0.35:
        risks.append("中文含义差异较大或缺失")
    if mapping_score < 0.35:
        risks.append("信号Mapping差异较大或缺失")

    return RenameCandidate(
        level="宽松候选",
        score=score,
        module=new_row.module,
        old_signal=old_row.signal,
        new_signal=new_row.signal,
        old_result_row=old_row.row_idx,
        new_result_row=new_row.row_idx,
        match_key=f"fuzzy:{old_row.row_idx}->{new_row.row_idx}",
        reasons="；".join(reasons),
        risks="；".join(risks),
    )


def _write_sheet(sheet: Worksheet, candidates: list[RenameCandidate]) -> None:
    headers = [
        "模块名字",
        "候选类型",
        "相似度分数",
        "判断结果",
        "旧版本Signal接口",
        "新版本Signal接口",
        "旧版本对比结果行号",
        "新版本对比结果行号",
        "匹配Key",
        "主要依据",
        "主要风险点",
        "人工确认结果",
        "备注",
    ]
    sheet.append(headers)
    for candidate in candidates:
        sheet.append(
            [
                candidate.module,
                candidate.level,
                candidate.score,
                "疑似Signal接口重命名",
                candidate.old_signal,
                candidate.new_signal,
                candidate.old_result_row,
                candidate.new_result_row,
                candidate.match_key,
                candidate.reasons,
                candidate.risks,
                "待确认",
                "",
            ]
        )
    _style_sheet(sheet)


def _write_direct_sheet(
    sheet: Worksheet,
    candidates: list[DirectRenameCandidate],
    source_headers: list[str],
) -> None:
    headers = source_headers + [
        "候选类型",
        "综合分",
        "版本行号",
        "主要依据",
        "主要风险点",
        "人工确认结果",
        "备注",
    ]
    sheet.append(headers)
    for candidate in candidates:
        paired_values = [
            _version_pair(new_value, old_value)
            for new_value, old_value in zip(candidate.new_row.values, candidate.old_row.values)
        ]
        sheet.append(
            paired_values
            + [
                "疑似Signal接口重命名",
                candidate.score,
                _version_pair(str(candidate.new_row.row_idx), str(candidate.old_row.row_idx)),
                candidate.reasons,
                candidate.risks,
                "待确认",
                "",
            ]
        )
    _style_direct_sheet(sheet)


def _style_direct_sheet(sheet: Worksheet) -> None:
    sheet.freeze_panes = "A2"
    if sheet.max_row >= 1:
        sheet.auto_filter.ref = f"A1:V{sheet.max_row}"
        validation = DataValidation(type="list", formula1='"同接口,非同接口,待确认"', allow_blank=True)
        validation.error = "请选择：同接口、非同接口或待确认"
        validation.errorTitle = "人工确认结果无效"
        sheet.add_data_validation(validation)
        validation.add(f"U2:U{max(sheet.max_row, 2)}")
    widths = {
        "A": 18,
        "B": 12,
        "C": 14,
        "D": 22,
        "E": 34,
        "F": 34,
        "G": 34,
        "H": 44,
        "I": 42,
        "J": 18,
        "K": 20,
        "L": 12,
        "M": 20,
        "N": 42,
        "O": 42,
        "P": 22,
        "Q": 10,
        "R": 18,
        "S": 54,
        "T": 36,
        "U": 16,
        "V": 24,
    }
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in sheet.iter_rows(min_row=2):
        for index, cell in enumerate(row, start=1):
            if index <= len(DIRECT_OUTPUT_SOURCE_COLUMNS) and not _paired_cell_values_equal(cell.value):
                cell.fill = DIFF_FILL
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        sheet.row_dimensions[row[0].row].height = 42
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for col_idx in range(1, 23):
        sheet.column_dimensions[get_column_letter(col_idx)].hidden = False


def _remove_cross_matches(candidates: list[DirectRenameCandidate]) -> list[DirectRenameCandidate]:
    pairs = {_direct_signal_pair(candidate) for candidate in candidates}
    return [
        candidate
        for candidate in candidates
        if tuple(reversed(_direct_signal_pair(candidate))) not in pairs
    ]


def _remove_same_module_upstream_matches(candidates: list[DirectRenameCandidate]) -> list[DirectRenameCandidate]:
    return [
        candidate
        for candidate in candidates
        if not (
            candidate.new_row.module == candidate.old_row.module
            and candidate.new_row.upstream == candidate.old_row.upstream
            and candidate.new_row.module == candidate.new_row.upstream
        )
    ]


def _context_key(row: SourceRow) -> tuple[str, ...]:
    return (
        row.module,
        row.direction,
        row.param_method,
        row.pp_topic,
        row.data_type_level1,
        row.data_type_level2,
        row.data_type,
    )


def _build_signal_presence(rows: list[SourceRow]) -> set[tuple[tuple[str, ...], str]]:
    return {
        (_context_key(row), _normalize_text(row.signal))
        for row in rows
        if _normalize_text(row.signal)
    }


def _is_coexisting_signal_pair(
    new_row: SourceRow,
    old_row: SourceRow,
    new_presence: set[tuple[tuple[str, ...], str]],
    old_presence: set[tuple[tuple[str, ...], str]],
) -> bool:
    context = _context_key(new_row)
    if context != _context_key(old_row):
        return False
    old_signal = _normalize_text(old_row.signal)
    new_signal = _normalize_text(new_row.signal)
    return (context, old_signal) in new_presence and (context, new_signal) in old_presence


def _direct_signal_pair(candidate: DirectRenameCandidate) -> tuple[str, str]:
    return (
        _normalize_text(candidate.old_row.signal),
        _normalize_text(candidate.new_row.signal),
    )


def _collect_source_rows(
    sheet: Worksheet,
    key_columns: tuple[int, ...],
    included_modules: frozenset[str] | None = None,
    excluded_row_indexes: set[int] | None = None,
) -> list[SourceRow]:
    rows: list[SourceRow] = []
    for row_idx in range(2, sheet.max_row + 1):
        if excluded_row_indexes is not None and row_idx in excluded_row_indexes:
            continue
        module = _cell_text(sheet.cell(row_idx, MODULE_COLUMN).value)
        if included_modules is not None and module not in included_modules:
            continue
        signal = _cell_text(sheet.cell(row_idx, SIGNAL_COLUMN).value)
        key_text = _normalized_key_for_row(sheet, row_idx, key_columns)
        if not signal or not key_text:
            continue
        rows.append(
            SourceRow(
                row_idx=row_idx,
                module=module,
                direction=_cell_text(sheet.cell(row_idx, SIGNAL_DIRECTION_COLUMN).value),
                param_method=_cell_text(sheet.cell(row_idx, PARAM_METHOD_COLUMN).value),
                upstream=_cell_text(sheet.cell(row_idx, SIGNAL_UPSTREAM_COLUMN).value),
                pp_topic=_cell_text(sheet.cell(row_idx, PP_TOPIC_COLUMN).value),
                data_type_level1=_cell_text(sheet.cell(row_idx, DATA_TYPE_LEVEL1_COLUMN).value),
                data_type_level2=_cell_text(sheet.cell(row_idx, DATA_TYPE_LEVEL2_COLUMN).value),
                data_type=_cell_text(sheet.cell(row_idx, DATA_TYPE_COLUMN).value),
                signal=signal,
                key_text=key_text,
                core_text=_format_core(_signal_core(signal)),
                values=tuple(_cell_text(sheet.cell(row_idx, col_idx).value) for col_idx in DIRECT_OUTPUT_SOURCE_COLUMNS),
            )
        )
    return rows


def _build_ngram_index(rows: list[SourceRow], config: RenameConfig) -> dict[str, list[int]]:
    raw_index: dict[str, list[int]] = defaultdict(list)
    for row_id, row in enumerate(rows):
        for ngram in _ngrams(row.key_text, config.ngram_size):
            raw_index[ngram].append(row_id)
    return {
        ngram: row_ids
        for ngram, row_ids in raw_index.items()
        if len(row_ids) <= config.max_ngram_bucket
    }


def _recall_old_candidates(
    new_row: SourceRow,
    old_rows: list[SourceRow],
    old_index: dict[str, list[int]],
    config: RenameConfig,
) -> list[int]:
    counts: Counter[int] = Counter()
    for ngram in _ngrams(new_row.key_text, config.ngram_size):
        counts.update(old_index.get(ngram, ()))
    if not counts:
        return []
    return [
        row_id
        for row_id, _ in counts.most_common(config.max_candidates_per_row)
        if row_id < len(old_rows)
    ]


def _score_source_pair(
    new_row: SourceRow,
    old_row: SourceRow,
    config: RenameConfig,
) -> DirectRenameCandidate | None:
    if not _same_required(new_row.module, old_row.module):
        return None
    if not _same_required(new_row.direction, old_row.direction):
        return None
    if not _same_required(new_row.data_type, old_row.data_type):
        return None
    if not _same_optional(new_row.pp_topic, old_row.pp_topic):
        return None
    if not _same_optional(new_row.data_type_level1, old_row.data_type_level1):
        return None
    if not _same_optional(new_row.data_type_level2, old_row.data_type_level2):
        return None
    if _normalize_text(new_row.signal) == _normalize_text(old_row.signal):
        return None
    key_similarity = SequenceMatcher(None, new_row.key_text, old_row.key_text).ratio()
    if key_similarity < config.key_threshold:
        return None
    signal_similarity = _lcs_similarity(new_row.core_text, old_row.core_text)
    if signal_similarity < config.signal_threshold:
        return None
    score = round(key_similarity * 60 + signal_similarity * 40)
    reasons = (
        "模块名、Signal方向、数据类型、E/F/G列一致；"
        "Signal接口疑似重命名"
    )
    risks: list[str] = []
    if key_similarity < 0.95:
        risks.append("Key相似但并非完全一致")
    if signal_similarity < 0.70:
        risks.append("Signal核心LCS相似度偏低")
    return DirectRenameCandidate(
        score=score,
        key_similarity=key_similarity,
        signal_similarity=signal_similarity,
        old_row=old_row,
        new_row=new_row,
        reasons=reasons,
        risks="；".join(risks),
    )


def _style_sheet(sheet: Worksheet) -> None:
    sheet.freeze_panes = "A2"
    if sheet.max_row >= 1:
        sheet.auto_filter.ref = f"A1:M{sheet.max_row}"
    widths = {
        "A": 18,
        "B": 14,
        "C": 12,
        "D": 22,
        "E": 34,
        "F": 34,
        "G": 18,
        "H": 18,
        "I": 48,
        "J": 64,
        "K": 40,
        "L": 16,
        "M": 24,
    }
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in sheet.iter_rows(min_row=2):
        fill = STRICT_FILL if row[1].value == "强匹配" else FUZZY_FILL
        for cell in row:
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        row[4].font = OLD_FONT
        row[5].font = NEW_FONT
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width


def _signal_core(value: str) -> list[str]:
    tokens = _tokens(value)
    core = [
        token
        for token in tokens
        if token not in NOISE_TOKENS
        and token not in TYPE_SUFFIX_TOKENS
        and token not in WEAK_SUFFIX_TOKENS
        and not _is_type_suffix_token(token)
    ]
    return core or [token for token in tokens if token not in TYPE_SUFFIX_TOKENS and not _is_type_suffix_token(token)] or tokens


def _columns_equal_ratio(sheet: Worksheet, new_row: int, old_row: int, columns: tuple[int, ...]) -> float:
    if not columns:
        return 0.0
    same = 0
    for col_idx in columns:
        if _cell_text(sheet.cell(new_row, col_idx).value) == _cell_text(sheet.cell(old_row, col_idx).value):
            same += 1
    return same / len(columns)


def _name_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    token_score = _ordered_token_overlap(left_tokens, right_tokens)
    text_score = SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()
    return token_score * 0.6 + text_score * 0.4


def _text_similarity(left: str, right: str) -> float:
    left = _normalize_text(left)
    right = _normalize_text(right)
    if not left and not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _ordered_token_overlap(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    jaccard = len(left_set & right_set) / len(left_set | right_set)
    sequence = SequenceMatcher(None, left, right).ratio()
    return jaccard * 0.6 + sequence * 0.4


def _lcs_similarity(left: str, right: str) -> float:
    left = _normalize_text(left)
    right = _normalize_text(right)
    if not left or not right:
        return 0.0
    previous = [0] * (len(right) + 1)
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    lcs_length = previous[-1]
    return lcs_length / max(len(left), len(right))


def _tokens(value: str) -> list[str]:
    parts: list[str] = []
    for chunk in SPLIT_RE.split(value):
        parts.extend(match.group(0).lower() for match in WORD_RE.finditer(chunk))
    return [part for part in parts if part]


def _normalized_key_for_row(sheet: Worksheet, row_idx: int, columns: tuple[int, ...]) -> str:
    return _normalize_key_text(
        "".join(_cell_text(sheet.cell(row_idx, col_idx).value) for col_idx in columns if col_idx > 0)
    )


def _direct_source_headers(sheet: Worksheet) -> list[str]:
    return [
        _cell_text(sheet.cell(1, col_idx).value) or f"列{col_idx}"
        for col_idx in DIRECT_OUTPUT_SOURCE_COLUMNS
    ]


def _version_pair(new_value: str, old_value: str) -> str:
    return f"NEW_VERSION:{new_value};\nOLD_VERSION:{old_value}"


def _paired_cell_values_equal(value: Any) -> bool:
    text = _cell_text(value)
    if "NEW_VERSION:" not in text or "OLD_VERSION:" not in text:
        return True
    return _extract_version_value(text, "NEW_VERSION:") == _extract_version_value(text, "OLD_VERSION:")


def _extract_version_value(text: str, prefix: str) -> str:
    start = text.find(prefix)
    if start < 0:
        return ""
    value_start = start + len(prefix)
    next_positions = [
        pos
        for other_prefix in ("NEW_VERSION:", "OLD_VERSION:")
        if other_prefix != prefix
        for pos in [text.find(other_prefix, value_start)]
        if pos >= 0
    ]
    value_end = min(next_positions) if next_positions else len(text)
    result = text[value_start:value_end].strip()
    if result.endswith(";"):
        result = result[:-1].strip()
    return result


def _same_required(new_value: str, old_value: str) -> bool:
    return bool(new_value) and bool(old_value) and new_value == old_value


def _same_optional(new_value: str, old_value: str) -> bool:
    return new_value == old_value


def _normalize_key_text(value: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value)).lower()


def _ngrams(value: str, size: int) -> set[str]:
    if not value:
        return set()
    size = max(1, size)
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(0, len(value) - size + 1)}


def _normalize_text(value: str) -> str:
    return "".join(match.group(0).lower() for match in WORD_RE.finditer(value))


def _is_type_suffix_token(token: str) -> bool:
    return token in {"u", "s", "w"} or token.isdigit()


def _combined_key(sheet: Worksheet, row_idx: int, columns: tuple[int, ...]) -> str:
    return "|".join(_cell_text(sheet.cell(row_idx, col_idx).value) for col_idx in columns)


def _identity(row: ResultRow) -> str:
    return f"{row.module}|{row.signal}|{row.row_idx}"


def _format_core(tokens: list[str]) -> str:
    return "".join(tokens) if tokens else "(空)"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
