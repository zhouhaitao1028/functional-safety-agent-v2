from __future__ import annotations

from collections import defaultdict
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Iterable
from zipfile import ZipFile
import hashlib
import json
import re

from openpyxl import load_workbook

from ..errors import ContractError


SIGNAL_SHEET = "信号和参数定义"
INTERNAL_CONFIG_SHEET = "内部单元和组件映射配置"
EXTERNAL_CONFIG_SHEET = "外部组件映射配置"
OVERVIEW_SHEET = "Sheet1"
MISSING_LINK_TEXT = "链路流信号流缺失"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class _PackageFiles(AbstractContextManager):
    """Read workbooks from either the legacy ZIP or an unpacked package."""

    def __init__(self, source: Path):
        self.source = source.resolve(strict=True)
        self._zip: ZipFile | None = None
        self._zip_entries = {}
        if self.source.is_file() and self.source.suffix.lower() == ".zip":
            self._zip = ZipFile(self.source)
            names = []
            for item in self._zip.infolist():
                if item.is_dir():
                    continue
                name = self._decode_zip_name(item).replace("\\", "/")
                self._zip_entries[name] = item
                names.append(name)
            self._names = tuple(names)
        elif self.source.is_dir():
            self._names = tuple(
                path.relative_to(self.source).as_posix()
                for path in self.source.rglob("*")
                if path.is_file()
            )
        else:
            raise ContractError(
                "software interface source must be a ZIP file or an unpacked directory"
            )

    def __exit__(self, exc_type, exc, traceback):
        if self._zip is not None:
            self._zip.close()
        return False

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    def workbook_source(self, name: str):
        if self._zip is not None:
            return BytesIO(self._zip.read(self._zip_entries[name]))
        return self.source / PurePosixPath(name)

    def read_bytes(self, name: str) -> bytes:
        if self._zip is not None:
            return self._zip.read(self._zip_entries[name])
        return (self.source / PurePosixPath(name)).read_bytes()

    @staticmethod
    def _decode_zip_name(item) -> str:
        if item.flag_bits & 0x800:
            return item.filename
        try:
            return item.filename.encode("cp437").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return item.filename

    def fingerprint(self) -> str:
        if self.source.is_file():
            return _sha256_file(self.source)
        digest = hashlib.sha256()
        for name in sorted(self.names):
            path = self.source / PurePosixPath(name)
            stat = path.stat()
            digest.update(
                f"{name}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8")
            )
        return digest.hexdigest()


class InterfaceAnalysisService:
    """Deterministic software-interface inventory, trace and diff service."""

    def analyze_package(
        self,
        source: Path | str,
        *,
        output_dir: Path | str | None = None,
        max_findings: int = 500,
    ) -> dict:
        max_findings = max(1, min(int(max_findings), 5000))
        source_path = Path(source)
        with _PackageFiles(source_path) as files:
            discovered = self._discover(files.names)
            if not discovered["module_workbooks"]:
                raise ContractError(
                    "no module interface workbooks found under Inputs/01_PIE_400"
                )

            internal_mapping = self._load_internal_mapping(
                files, discovered.get("current_config")
            )
            records: list[dict] = []
            skipped: list[dict] = []
            module_status: list[dict] = []
            for name in discovered["module_workbooks"]:
                basename = PurePosixPath(name).name
                mapping = self._resolve_module_mapping(basename, internal_mapping)
                try:
                    module_records = self._scan_module_workbook(
                        files, name, mapping
                    )
                    records.extend(module_records)
                    module_status.append(
                        {
                            "file": basename,
                            "records": len(module_records),
                            "mapped": bool(mapping),
                            "software_unit": mapping.get("software_unit", "")
                            if mapping
                            else "",
                            "component": mapping.get("component", "")
                            if mapping
                            else "",
                        }
                    )
                except Exception as exc:
                    skipped.append(
                        {
                            "file": basename,
                            "reason": f"{type(exc).__name__}: {exc}",
                        }
                    )

            findings: list[dict] = []
            for item in module_status:
                if not item["mapped"]:
                    findings.append(
                        self._finding(
                            "unmapped_module",
                            "warning",
                            f"模块文件未匹配 Unit2ComponentConfig：{item['file']}",
                            {"file": item["file"]},
                        )
                    )
            for item in skipped:
                findings.append(
                    self._finding(
                        "workbook_parse_error",
                        "error",
                        f"模块接口表读取失败：{item['file']}",
                        item,
                    )
                )
            input_finding_count = len(findings)

            matrix = self._scan_pie_matrix(
                files,
                discovered.get("pie_matrix"),
                records,
                max_findings=max_findings,
            )
            findings.extend(matrix.pop("findings"))
            matrix_finding_count = matrix.pop("total_findings")

            overview_name = discovered.get("overview")
            if overview_name:
                overview = self._inspect_overview_source(
                    files,
                    overview_name,
                    max_findings=max_findings,
                )
                mode = "legacy_overview_audit"
            else:
                overview = self._build_native_link_summary(
                    records, max_findings=max_findings
                )
                mode = "native_basic_match"
            overview_findings = overview.pop("findings")
            overview_finding_count = overview.pop("total_findings")
            findings.extend(overview_findings)

            config_diff = {}
            config_finding_count = 0
            current_config = discovered.get("current_config")
            baseline_config = discovered.get("baseline_config")
            if current_config and baseline_config:
                config_diff = self._compare_config_sources(
                    files, current_config, files, baseline_config
                )
                config_findings = config_diff.pop("findings")
                config_finding_count = config_diff.pop("total_findings")
                findings.extend(config_findings)

            duplicate_keys = self._duplicate_mapping_keys(records)
            for key, count in duplicate_keys[: max(0, max_findings - len(findings))]:
                findings.append(
                    self._finding(
                        "duplicate_mapping_key",
                        "warning",
                        f"接口映射键出现 {count} 次，需要确认是否为合法一对多关系",
                        {"mapping_key": key, "count": count},
                    )
                )

            total_findings = (
                input_finding_count
                + matrix_finding_count
                + overview_finding_count
                + config_finding_count
                + len(duplicate_keys)
            )
            findings = findings[:max_findings]
            report = {
                "schema_version": "1.0",
                "generated_at": _utc_now(),
                "mode": mode,
                "source": str(files.source),
                "source_fingerprint": files.fingerprint(),
                "inputs": {
                    "module_workbooks": len(discovered["module_workbooks"]),
                    "pie_matrix": discovered.get("pie_matrix", ""),
                    "unit_config": current_config or "",
                    "baseline_config": baseline_config or "",
                    "existing_overview": overview_name or "",
                },
                "summary": {
                    "module_files": len(discovered["module_workbooks"]),
                    "parsed_module_files": len(module_status),
                    "skipped_module_files": len(skipped),
                    "mapped_module_files": sum(
                        1 for item in module_status if item["mapped"]
                    ),
                    "unmapped_module_files": sum(
                        1 for item in module_status if not item["mapped"]
                    ),
                    "extracted_interface_records": len(records),
                    "pie_matrix_rows": matrix["rows"],
                    "pie_matrix_mapping_keys": matrix["mapping_keys"],
                    "matrix_matched_interface_records": matrix[
                        "matched_interface_records"
                    ],
                    "matrix_unmatched_interface_records": matrix[
                        "unmatched_interface_records"
                    ],
                    "overview_records": overview["overview_records"],
                    "cross_application_links": overview[
                        "cross_application_links"
                    ],
                    "valid_cross_application_links": overview[
                        "valid_cross_application_links"
                    ],
                    "missing_cross_application_links": overview[
                        "missing_cross_application_links"
                    ],
                    "duplicate_mapping_keys": len(duplicate_keys),
                    "findings": total_findings,
                },
                "configuration_diff": config_diff,
                "findings": findings,
                "findings_truncated": total_findings > len(findings),
                "artifacts": [],
                "limitations": self._limitations(mode),
            }
            if output_dir is not None:
                self._write_outputs(Path(output_dir), report, records)
            return report

    def compare_config_files(
        self,
        current: Path | str,
        baseline: Path | str,
        *,
        output: Path | str | None = None,
    ) -> dict:
        current_path = Path(current).resolve(strict=True)
        baseline_path = Path(baseline).resolve(strict=True)
        result = self._compare_config_paths(current_path, baseline_path)
        value = {
            "schema_version": "1.0",
            "generated_at": _utc_now(),
            "current": str(current_path),
            "baseline": str(baseline_path),
            **result,
        }
        if output is not None:
            self._write_json_atomic(Path(output), value)
        return value

    def inspect_overview_file(
        self, path: Path | str, *, max_findings: int = 500
    ) -> dict:
        value = Path(path).resolve(strict=True)
        with _PackageFiles(value.parent) as files:
            result = self._inspect_overview_source(
                files, value.name, max_findings=max_findings
            )
        return {
            "schema_version": "1.0",
            "generated_at": _utc_now(),
            "overview": str(value),
            **result,
        }

    @staticmethod
    def _discover(names: Iterable[str]) -> dict:
        normalized = sorted(name.replace("\\", "/") for name in names)
        modules = [
            name
            for name in normalized
            if "/Inputs/01_PIE_400/" in f"/{name}"
            and name.lower().endswith((".xlsm", ".xlsx"))
            and not PurePosixPath(name).name.startswith("~$")
        ]
        matrices = [
            name
            for name in normalized
            if "/Inputs/02_PIE_Matrix/" in f"/{name}"
            and PurePosixPath(name).name.startswith("PIE_Matrix_Res_")
            and name.lower().endswith(".xlsx")
        ]
        configs = [
            name
            for name in normalized
            if "/Inputs/03_UnitConfig/" in f"/{name}"
            and name.lower().endswith((".xlsx", ".xlsm"))
        ]
        current = next(
            (
                name
                for name in configs
                if PurePosixPath(name).name.lower()
                == "unit2componentconfig.xlsx"
            ),
            None,
        )
        baselines = [
            name
            for name in configs
            if re.fullmatch(
                r"Unit2ComponentConfig\(.+\)\.xlsx",
                PurePosixPath(name).name,
                re.IGNORECASE,
            )
        ]
        overviews = [
            name
            for name in normalized
            if "/Outputs/01_接口收发表/" in f"/{name}"
            and PurePosixPath(name).name.startswith("软件组件接口收发总览表")
            and name.lower().endswith(".xlsx")
        ]
        timestamped = [
            item
            for item in overviews
            if re.search(r"_\d{8}_\d{6}\.xlsx$", item)
        ]
        return {
            "module_workbooks": modules,
            "pie_matrix": max(matrices) if matrices else None,
            "current_config": current,
            "baseline_config": max(baselines) if baselines else None,
            "overview": max(timestamped or overviews) if overviews else None,
        }

    def _scan_module_workbook(
        self, files: _PackageFiles, name: str, mapping: dict | None
    ) -> list[dict]:
        workbook = load_workbook(
            files.workbook_source(name),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        try:
            sheet_name = next(
                (
                    title
                    for title in workbook.sheetnames
                    if title.strip() == SIGNAL_SHEET
                ),
                None,
            )
            if not sheet_name:
                raise ContractError(f"missing worksheet {SIGNAL_SHEET}")
            sheet = workbook[sheet_name]
            header_row, headers = self._detect_header(sheet)
            records = []
            for row_number, row in enumerate(
                sheet.iter_rows(
                    min_row=header_row + 1,
                    max_col=min(max(sheet.max_column, 13), 50),
                    values_only=True,
                ),
                start=header_row + 1,
            ):
                signal_name = self._row_value(row, headers, "Name", 1)
                if not signal_name:
                    continue
                storage_class = self._row_value(
                    row, headers, "Storage Class", 3
                )
                direction = self._direction(storage_class)
                if direction == "Unknown":
                    continue
                top_type = self._row_value(
                    row, headers, "TopDataTypeName", 11
                )
                full_element = self._row_value(
                    row, headers, "FullElementName", 12
                )
                port_name = self._row_value(
                    row,
                    headers,
                    "InportName/SourceBLckName/OutportName",
                    13,
                )
                mapping_key = "|".join((port_name, top_type, full_element))
                record_id = hashlib.sha1(
                    f"{PurePosixPath(name).name}|{row_number}|{signal_name}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
                records.append(
                    {
                        "id": f"IF-{record_id}",
                        "module_file": PurePosixPath(name).name,
                        "row": row_number,
                        "name": signal_name,
                        "type": self._row_value(row, headers, "Type", 2),
                        "direction": direction,
                        "storage_class": storage_class,
                        "data_type": self._row_value(
                            row, headers, "DataType", 4
                        ),
                        "description": self._row_value(
                            row, headers, "Description", 7
                        ),
                        "dimension": self._row_value(
                            row, headers, "Dimension", 8
                        ),
                        "coding": self._row_value(row, headers, "coding", 14),
                        "top_data_type_name": top_type,
                        "full_element_name": full_element,
                        "port_name": port_name,
                        "mapping_key": mapping_key,
                        "software_unit": mapping.get("software_unit", "")
                        if mapping
                        else "",
                        "component": mapping.get("component", "")
                        if mapping
                        else "",
                        "swc": mapping.get("swc", "") if mapping else "",
                    }
                )
            return records
        finally:
            workbook.close()

    @staticmethod
    def _detect_header(sheet) -> tuple[int, dict[str, int]]:
        for row_number, row in enumerate(
            sheet.iter_rows(min_row=1, max_row=min(20, sheet.max_row), values_only=True),
            start=1,
        ):
            values = [_clean(value) for value in row]
            if "Name" in values and (
                "Storage Class" in values or "DataType" in values
            ):
                return row_number, {
                    value: index for index, value in enumerate(values) if value
                }
        # A few legacy module dictionaries have an intentionally blank row 8
        # but keep the same fixed A:N field layout and start data on row 9.
        if sheet.max_row >= 9:
            probe = next(
                sheet.iter_rows(min_row=9, max_row=9, max_col=4, values_only=True)
            )
            if _clean(probe[0]) and _clean(probe[1]) in {"Signal", "Parameter"}:
                fixed = (
                    "Name",
                    "Type",
                    "Storage Class",
                    "DataType",
                    "Min",
                    "Max",
                    "Description",
                    "Dimension",
                    "Initial",
                    "Value",
                    "TopDataTypeName",
                    "FullElementName",
                    "InportName/SourceBLckName/OutportName",
                    "coding",
                )
                return 8, {name: index for index, name in enumerate(fixed)}
        raise ContractError("cannot locate interface header row")

    @staticmethod
    def _row_value(
        row: tuple, headers: dict[str, int], header: str, fallback_column: int
    ) -> str:
        index = headers.get(header, fallback_column - 1)
        return _clean(row[index]) if 0 <= index < len(row) else ""

    @staticmethod
    def _direction(storage_class: str) -> str:
        value = storage_class.casefold()
        if "input" in value or "import" in value:
            return "Input"
        if "output" in value or "export" in value:
            return "Output"
        return "Unknown"

    def _load_internal_mapping(
        self, files: _PackageFiles, config_name: str | None
    ) -> dict:
        if not config_name:
            return {"by_file": {}, "rows": []}
        rows = self._load_config_sheet(files, config_name, INTERNAL_CONFIG_SHEET)
        by_file = {}
        for item in rows:
            filename = item.get("XLSM文件名", "")
            if filename:
                by_file[filename.casefold()] = {
                    "swc": item.get("软件SWC名", ""),
                    "component": item.get("软件组件名", ""),
                    "software_unit": item.get("软件单元名", ""),
                    "file": filename,
                }
        return {"by_file": by_file, "rows": rows}

    @staticmethod
    def _resolve_module_mapping(basename: str, mapping: dict) -> dict | None:
        exact = mapping.get("by_file", {}).get(basename.casefold())
        if exact:
            return exact
        candidates = []
        for row in mapping.get("rows", []):
            unit = row.get("软件单元名", "")
            if unit and basename.casefold().startswith(unit.casefold()):
                candidates.append(
                    {
                        "swc": row.get("软件SWC名", ""),
                        "component": row.get("软件组件名", ""),
                        "software_unit": unit,
                        "file": row.get("XLSM文件名", ""),
                    }
                )
        if not candidates:
            return None
        return max(candidates, key=lambda item: len(item["software_unit"]))

    def _inspect_overview_source(
        self,
        files: _PackageFiles,
        name: str,
        *,
        max_findings: int,
    ) -> dict:
        workbook = load_workbook(
            files.workbook_source(name),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
        try:
            if OVERVIEW_SHEET not in workbook.sheetnames:
                raise ContractError(f"overview workbook has no {OVERVIEW_SHEET}")
            sheet = workbook[OVERVIEW_SHEET]
            header_values = next(
                sheet.iter_rows(min_row=1, max_row=1, values_only=True)
            )
            headers = {
                _clean(value): index
                for index, value in enumerate(header_values)
                if _clean(value)
            }
            record_index = headers.get("序号", 0)
            signal_index = headers.get("Signal接口", 8)
            module_index = headers.get("模块名字", 1)
            direction_index = headers.get("Signal方向", 2)
            swc_link_index = headers.get("跨应用层SWC链路流", 26)
            signal_link_index = headers.get("跨应用层SWC信号流", 27)
            total = cross = missing = 0
            findings = []
            for row_number, row in enumerate(
                sheet.iter_rows(min_row=2, max_col=28, values_only=True), start=2
            ):
                record_id = (
                    _clean(row[record_index]) if record_index < len(row) else ""
                )
                signal = _clean(row[signal_index]) if signal_index < len(row) else ""
                if not record_id and not signal:
                    continue
                total += 1
                swc_link = (
                    _clean(row[swc_link_index])
                    if swc_link_index < len(row)
                    else ""
                )
                signal_link = (
                    _clean(row[signal_link_index])
                    if signal_link_index < len(row)
                    else ""
                )
                if not swc_link and not signal_link:
                    continue
                cross += 1
                if MISSING_LINK_TEXT in swc_link or MISSING_LINK_TEXT in signal_link:
                    missing += 1
                    if len(findings) < max_findings:
                        findings.append(
                            self._finding(
                                "missing_cross_application_link",
                                "error",
                                f"跨应用层链路缺失：{signal}",
                                {
                                    "workbook": PurePosixPath(name).name,
                                    "sheet": OVERVIEW_SHEET,
                                    "row": row_number,
                                    "module": _clean(row[module_index]),
                                    "direction": _clean(row[direction_index]),
                                    "signal": signal,
                                },
                            )
                        )
            return {
                "overview_records": total,
                "cross_application_links": cross,
                "valid_cross_application_links": cross - missing,
                "missing_cross_application_links": missing,
                "total_findings": missing,
                "findings": findings,
            }
        finally:
            workbook.close()

    def _build_native_link_summary(
        self, records: list[dict], *, max_findings: int
    ) -> dict:
        groups: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            key = record["mapping_key"]
            if key and key != "||":
                groups[key].append(record)
        cross = valid = missing = 0
        findings = []
        for key, items in groups.items():
            inputs = [item for item in items if item["direction"] == "Input"]
            outputs = [item for item in items if item["direction"] == "Output"]
            components = {item["component"] for item in items if item["component"]}
            if len(components) < 2:
                continue
            cross += 1
            if inputs and outputs:
                valid += 1
            else:
                missing += 1
                if len(findings) < max_findings:
                    findings.append(
                        self._finding(
                            "missing_cross_application_link",
                            "error",
                            "接口映射键只有单向记录，无法闭合收发链路",
                            {
                                "mapping_key": key,
                                "directions": sorted(
                                    {item["direction"] for item in items}
                                ),
                                "components": sorted(components),
                            },
                        )
                    )
        return {
            "overview_records": len(records),
            "cross_application_links": cross,
            "valid_cross_application_links": valid,
            "missing_cross_application_links": missing,
            "total_findings": missing,
            "findings": findings,
        }

    def _scan_pie_matrix(
        self,
        files: _PackageFiles,
        matrix_name: str | None,
        records: list[dict],
        *,
        max_findings: int,
    ) -> dict:
        if not matrix_name:
            return {
                "rows": 0,
                "mapping_keys": 0,
                "matched_interface_records": 0,
                "unmatched_interface_records": 0,
                "total_findings": 0,
                "findings": [],
            }
        workbook = load_workbook(
            files.workbook_source(matrix_name),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
        try:
            keys: set[str] = set()
            row_count = 0
            for sheet in workbook.worksheets:
                rows = sheet.iter_rows(values_only=True)
                try:
                    header = [_clean(value) for value in next(rows)]
                except StopIteration:
                    continue
                headers = {
                    value: index for index, value in enumerate(header) if value
                }
                port_index = headers.get(
                    "InportName_SourceBlockName_OutportName", 1
                )
                top_index = headers.get("TopDataTypeName", 3)
                element_index = headers.get("FullElementName", 4)
                for row in rows:
                    port = _clean(row[port_index]) if port_index < len(row) else ""
                    top = _clean(row[top_index]) if top_index < len(row) else ""
                    element = (
                        _clean(row[element_index])
                        if element_index < len(row)
                        else ""
                    )
                    if not port and not top and not element:
                        continue
                    row_count += 1
                    keys.add("|".join((port, top, element)))
            matched = 0
            unmatched_records = []
            for record in records:
                key = record["mapping_key"]
                if not key or key == "||":
                    continue
                if key in keys:
                    matched += 1
                else:
                    unmatched_records.append(record)
            findings = [
                self._finding(
                    "missing_pie_matrix_mapping",
                    "warning",
                    f"接口映射键未在 PIE Matrix 中找到：{item['name']}",
                    {
                        "module_file": item["module_file"],
                        "row": item["row"],
                        "signal": item["name"],
                        "mapping_key": item["mapping_key"],
                    },
                )
                for item in unmatched_records[:max_findings]
            ]
            return {
                "rows": row_count,
                "mapping_keys": len(keys),
                "matched_interface_records": matched,
                "unmatched_interface_records": len(unmatched_records),
                "total_findings": len(unmatched_records),
                "findings": findings,
            }
        finally:
            workbook.close()

    @staticmethod
    def _duplicate_mapping_keys(records: list[dict]) -> list[tuple[str, int]]:
        counts: dict[str, int] = defaultdict(int)
        for record in records:
            key = record["mapping_key"]
            if key and key != "||":
                counts[key] += 1
        return sorted(
            ((key, count) for key, count in counts.items() if count > 1),
            key=lambda item: (-item[1], item[0]),
        )

    def _compare_config_sources(
        self,
        current_files: _PackageFiles,
        current_name: str,
        baseline_files: _PackageFiles,
        baseline_name: str,
    ) -> dict:
        findings = []
        sections = {}
        for section, sheet, key_headers, compared_headers in (
            (
                "internal",
                INTERNAL_CONFIG_SHEET,
                ("软件单元名",),
                ("软件SWC名", "软件组件名", "软件单元名"),
            ),
            (
                "external",
                EXTERNAL_CONFIG_SHEET,
                ("匹配字符串",),
                (
                    "匹配字符串",
                    "映射结果",
                ),
            ),
        ):
            current_rows = self._load_config_sheet(
                current_files, current_name, sheet
            )
            baseline_rows = self._load_config_sheet(
                baseline_files, baseline_name, sheet
            )
            result = self._compare_rows(
                current_rows, baseline_rows, key_headers, compared_headers
            )
            sections[section] = result["summary"]
            for change in result["changes"]:
                findings.append(
                    self._finding(
                        f"{section}_config_{change['status']}",
                        "warning" if change["status"] != "removed" else "error",
                        f"{section} 配置 {change['status']}：{change['key']}",
                        change,
                    )
                )
        return {
            **sections,
            "total_findings": sum(
                item["added"] + item["removed"] + item["changed"]
                for item in sections.values()
            ),
            "findings": findings,
        }

    def _compare_config_paths(self, current: Path, baseline: Path) -> dict:
        with _PackageFiles(current.parent) as current_files, _PackageFiles(
            baseline.parent
        ) as baseline_files:
            return self._compare_config_sources(
                current_files,
                current.name,
                baseline_files,
                baseline.name,
            )

    @staticmethod
    def _compare_rows(
        current_rows: list[dict],
        baseline_rows: list[dict],
        key_headers: tuple[str, ...],
        compared_headers: tuple[str, ...],
    ) -> dict:
        def row_key(row: dict) -> str:
            return " | ".join(row.get(name, "") for name in key_headers)

        current: dict[str, list[dict]] = defaultdict(list)
        baseline: dict[str, list[dict]] = defaultdict(list)
        for row in current_rows:
            if any(row.get(name, "") for name in key_headers):
                current[row_key(row)].append(row)
        for row in baseline_rows:
            if any(row.get(name, "") for name in key_headers):
                baseline[row_key(row)].append(row)
        changes = []
        added = removed = changed = unchanged = 0
        for key in sorted(current.keys() | baseline.keys()):
            current_group = list(current.get(key, []))
            baseline_group = list(baseline.get(key, []))
            remaining_current = []
            for row in current_group:
                signature = tuple(row.get(name, "") for name in compared_headers)
                match_index = next(
                    (
                        index
                        for index, candidate in enumerate(baseline_group)
                        if tuple(
                            candidate.get(name, "") for name in compared_headers
                        )
                        == signature
                    ),
                    None,
                )
                if match_index is None:
                    remaining_current.append(row)
                else:
                    unchanged += 1
                    baseline_group.pop(match_index)
            paired = min(len(remaining_current), len(baseline_group))
            for index in range(paired):
                before_row = baseline_group[index]
                after_row = remaining_current[index]
                changed += 1
                changes.append(
                    {
                        "status": "changed",
                        "key": key,
                        "before": {
                            name: before_row.get(name, "")
                            for name in compared_headers
                        },
                        "after": {
                            name: after_row.get(name, "")
                            for name in compared_headers
                        },
                    }
                )
            for row in remaining_current[paired:]:
                added += 1
                changes.append({"status": "added", "key": key, "after": row})
            for row in baseline_group[paired:]:
                removed += 1
                changes.append(
                    {"status": "removed", "key": key, "before": row}
                )
        return {
            "summary": {
                "added": added,
                "removed": removed,
                "changed": changed,
                "unchanged": unchanged,
            },
            "changes": changes,
        }

    @staticmethod
    def _load_config_sheet(
        files: _PackageFiles, workbook_name: str, sheet_name: str
    ) -> list[dict]:
        workbook = load_workbook(
            files.workbook_source(workbook_name),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
        try:
            if sheet_name not in workbook.sheetnames:
                return []
            sheet = workbook[sheet_name]
            rows = sheet.iter_rows(values_only=True)
            header = [_clean(value) for value in next(rows)]
            output = []
            for row in rows:
                item = {
                    header[index]: _clean(value)
                    for index, value in enumerate(row)
                    if index < len(header) and header[index]
                }
                if any(item.values()):
                    output.append(item)
            return output
        finally:
            workbook.close()

    @staticmethod
    def _finding(
        category: str, severity: str, message: str, evidence: dict
    ) -> dict:
        digest = hashlib.sha1(
            json.dumps(
                [category, message, evidence],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:12]
        return {
            "id": f"IF-FINDING-{digest}",
            "category": category,
            "severity": severity,
            "message": message,
            "evidence": evidence,
            "requires_human_confirmation": severity in {"warning", "error"},
        }

    @staticmethod
    def _limitations(mode: str) -> list[str]:
        common = [
            "分析服务不会执行压缩包中的 EXE 或 VBA 宏。",
            "模糊名称匹配和业务合理性判断必须由工程师确认。",
        ]
        if mode == "native_basic_match":
            common.append(
                "未发现既有接口总览时，只按结构化映射键执行基础收发匹配；这不等价于旧工具的全部多级映射规则。"
            )
        return common

    def _write_outputs(
        self, output_dir: Path, report: dict, records: list[dict]
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        inventory_path = output_dir / "software_interface_inventory.jsonl"
        inventory_temp = inventory_path.with_suffix(".jsonl.tmp")
        with inventory_temp.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        inventory_temp.replace(inventory_path)
        report["artifacts"].append(
            {
                "type": "software_interface_inventory",
                "path": str(inventory_path.resolve()),
                "sha256": _sha256_file(inventory_path),
            }
        )
        report_path = output_dir / "software_interface_analysis.json"
        self._write_json_atomic(report_path, report)

    @staticmethod
    def _write_json_atomic(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
