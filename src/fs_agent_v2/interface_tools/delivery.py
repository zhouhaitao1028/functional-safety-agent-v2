from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from time import perf_counter
from zipfile import BadZipFile, ZipFile, ZipInfo
import hashlib
import json
import os
import shutil
import uuid

from openpyxl import load_workbook

from ..errors import ContractError
from .fast_generation import FastInterfaceOverviewGenerator
from .legacy_compare.change_overview import generate_change_overview
from .legacy_compare.models import CompareConfig, RenameConfig
from .legacy_compare.vba_compatible_compare import compare_source_workbooks


SUMMARY_NAME = "软件组件接口收发总览表.xlsx"
SUMMARY_PREFIX = "软件组件接口收发总览表"
CURRENT_CONFIG = "Unit2ComponentConfig.xlsx"
EXTERNAL_CONFIG_SHEET = "外部组件映射配置"
SUMMARY_SHEET = "Sheet1"
MISSING_FLOW_TEXT = "链路流信号流缺失"
EXCLUDE_RESULT = "剔除,不做任何处理"
ADAPTER_MODULES = frozenset(
    {
        "ACF-ACFService",
        "FM-FMService",
        "MCF-MCFService",
        "RCF-RCFService",
        "FMB-FMBService",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _cached_sha256(path_text: str, size: int, modified_ns: int) -> str:
    del size, modified_ns
    return _sha256(Path(path_text))


def _stable_sha256(path: Path) -> str:
    stat = path.stat()
    return _cached_sha256(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


class InterfaceDeliveryService:
    """Generate and compare interface overview deliveries (pure Python)."""

    def __init__(
        self,
        project_root: Path | None = None,
        template_path: Path | None = None,
    ):
        self.project_root = (
            project_root or Path(__file__).resolve().parents[3]
        ).resolve()
        configured_template = os.environ.get("FS_INTERFACE_TEMPLATE", "").strip()
        self.default_template = (
            Path(configured_template)
            if configured_template
            else template_path
            or self.project_root
            / "assets"
            / "software_interface"
            / SUMMARY_NAME
        ).resolve()

    def generate_overview(
        self,
        source: Path | str,
        *,
        output_dir: Path | str,
        engine: str = "fast",
        visible: bool = False,
        compare_to_existing: bool = False,
        timeout_seconds: int = 1200,
        max_evidence: int = 1000,
    ) -> dict:
        source_path = Path(source).resolve(strict=True)
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        requested_engine = _text(engine).casefold() or "fast"
        if requested_engine == "auto":
            requested_engine = "fast"
        if requested_engine != "fast":
            raise ContractError(
                "the legacy Excel COM generation engine has been retired; "
                "use engine='fast'"
            )
        timeout_seconds = max(60, min(int(timeout_seconds), 7200))
        max_evidence = max(1, min(int(max_evidence), 5000))
        started_at = perf_counter()
        run_id = "IF-GEN-" + uuid.uuid4().hex[:12]

        with TemporaryDirectory(
            prefix="fusa-interface-generation-",
            dir=output,
            ignore_cleanup_errors=True,
        ) as raw:
            work_root = Path(raw)
            package_root = work_root / "package"
            package = self._stage_package(source_path, package_root)
            config_file = package_root / "Inputs" / "03_UnitConfig" / CURRENT_CONFIG
            baseline = self._select_overview(package_root)
            template = self._select_template(package_root)
            template_metadata = {
                "source": (
                    "input_package"
                    if package_root in template.parents
                    else "controlled_default"
                ),
                "path": str(template),
                "sha256": _stable_sha256(template),
            }
            template_dir = package_root / "Templates"
            template_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template, template_dir / SUMMARY_NAME)

            manifest = self._input_manifest(package_root)
            manifest_path = output / f"{run_id}_input_manifest.jsonl"
            _write_jsonl(manifest_path, manifest)
            try:
                generated_source, generation_stats = (
                    FastInterfaceOverviewGenerator().generate(package_root)
                )
                generation_logs = [
                    "Pure-Python generation completed.",
                    json.dumps(generation_stats, ensure_ascii=False),
                ]
            except Exception as exc:
                diagnostic_path = output / f"{run_id}_failure_diagnostics.json"
                diagnostic = {
                    "schema_version": "1.0",
                    "operation": "generate_interface_overview",
                    "run_id": run_id,
                    "failed_at": _utc_now(),
                    "source": str(source_path),
                    "engine": requested_engine,
                    "error": str(exc),
                }
                _write_json(diagnostic_path, diagnostic)
                raise ContractError(
                    f"{exc}; diagnostics: {diagnostic_path}"
                ) from exc
            if not generated_source.is_file():
                raise ContractError(
                    f"generation reported a missing output: {generated_source}"
                )
            generated_target = self._unique_target(output, generated_source.name)
            shutil.copy2(generated_source, generated_target)
            config_snapshot = self._unique_target(
                output, f"{run_id}_Unit2ComponentConfig.xlsx"
            )
            shutil.copy2(config_file, config_snapshot)
            log_path = output / f"{run_id}_{requested_engine}_generation.log"
            log_path.write_text(
                "\n".join(generation_logs), encoding="utf-8"
            )

            validation = self._validate_generated_overview(
                generated_target,
                config_snapshot,
                max_evidence=max_evidence,
            )
            validation_path = output / f"{run_id}_validation.json"
            _write_json(validation_path, validation)

            comparison = None
            if compare_to_existing and baseline is not None:
                baseline_snapshot = self._unique_target(
                    output, f"{run_id}_baseline_{baseline.name}"
                )
                shutil.copy2(baseline, baseline_snapshot)
                comparison = self.compare_overviews(
                    generated_target,
                    baseline_snapshot,
                    output_dir=output,
                    timeout_seconds=min(timeout_seconds, 1800),
                    max_evidence=max_evidence,
                )

        artifacts = [
            self._artifact("software_interface_overview", generated_target),
            self._artifact("generation_input_manifest", manifest_path),
            self._artifact("generation_validation", validation_path),
            self._artifact("generated_unit_config_snapshot", config_snapshot),
            self._artifact(f"{requested_engine}_generation_log", log_path),
        ]
        if comparison:
            artifacts.extend(comparison["artifacts"])
        report = {
            "schema_version": "1.0",
            "operation": "generate_interface_overview",
            "run_id": run_id,
            "generated_at": _utc_now(),
            "duration_seconds": round(perf_counter() - started_at, 3),
            "source": str(source_path),
            "source_sha256": self._source_fingerprint(source_path),
            "template": template_metadata,
            "engine": requested_engine,
            "engine_details": {
                "implementation": "pure_python_reimplementation_of_recovered_vba",
                "visible_requested": bool(visible),
                "generation_stats": generation_stats,
                "fast_source_sha256": _stable_sha256(
                    Path(__file__).with_name("fast_generation.py")
                ),
            },
            "package": package,
            "validation": validation,
            "comparison_to_existing": comparison,
            "artifacts": artifacts,
            "requires_human_confirmation": (
                not validation["passed"]
                or validation["summary"]["missing_link_rows"] > 0
                or bool(comparison and comparison["summary"]["total_diffs"] > 0)
            ),
        }
        report_path = output / f"{run_id}_generation_report.json"
        report["report_path"] = str(report_path.resolve())
        _write_json(report_path, report)
        report["report_artifact"] = self._artifact("generation_report", report_path)
        if not validation["passed"]:
            raise ContractError(
                "generated interface overview failed independent validation; "
                f"see {validation_path}"
            )
        return report

    def compare_overviews(
        self,
        new_overview: Path | str,
        old_overview: Path | str,
        *,
        output_dir: Path | str,
        new_sheet: str = SUMMARY_SHEET,
        old_sheet: str = SUMMARY_SHEET,
        included_modules: list[str] | None = None,
        timeout_seconds: int = 600,
        max_evidence: int = 1000,
    ) -> dict:
        new_path = Path(new_overview).resolve(strict=True)
        old_path = Path(old_overview).resolve(strict=True)
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        timeout_seconds = max(30, min(int(timeout_seconds), 3600))
        max_evidence = max(1, min(int(max_evidence), 5000))
        started_at = perf_counter()
        run_id = "IF-DIFF-" + uuid.uuid4().hex[:12]
        target = self._unique_target(
            output,
            f"软件接口总览表差异_{datetime.now():%Y%m%d_%H%M%S}.xlsx",
        )
        worker_result = self._compare_source_workbooks(
            new_path,
            new_sheet,
            old_path,
            old_sheet,
            target,
            included_modules,
        )
        evidence, status_counts, rename_candidates = self._comparison_evidence(
            target, max_evidence=max_evidence
        )
        evidence_path = output / f"{run_id}_evidence.jsonl"
        _write_jsonl(evidence_path, evidence)
        comparison = worker_result["comparison"]
        content_multiset = self._content_multiset_parity(
            new_path,
            old_path,
            new_sheet=new_sheet,
            old_sheet=old_sheet,
            max_evidence=min(max_evidence, 200),
        )
        summary = {
            "new_rows": comparison["sheet1_rows"],
            "old_rows": comparison["sheet2_rows"],
            "total_diffs": comparison["total_diffs"],
            "new_only": comparison["sheet1_only"],
            "old_only": comparison["sheet2_only"],
            "content_diff_cells": comparison["content_diff_cells"],
            "duplicate_new_keys": comparison.get("duplicate_new_keys", 0),
            "duplicate_new_rows": comparison.get("duplicate_new_rows", 0),
            "duplicate_old_keys": comparison.get("duplicate_old_keys", 0),
            "duplicate_old_rows": comparison.get("duplicate_old_rows", 0),
            "rename_candidates": rename_candidates,
            "status_counts": status_counts,
        }
        report = {
            "schema_version": "1.0",
            "operation": "compare_interface_overviews",
            "run_id": run_id,
            "generated_at": _utc_now(),
            "duration_seconds": round(perf_counter() - started_at, 3),
            "new": {
                "path": str(new_path),
                "sheet": new_sheet,
                "sha256": _sha256(new_path),
            },
            "old": {
                "path": str(old_path),
                "sheet": old_sheet,
                "sha256": _sha256(old_path),
            },
            "comparison_key_columns": comparison["key_columns"],
            "summary": summary,
            "change_overview": worker_result["change_overview"],
            "content_multiset_parity": content_multiset,
            "evidence_truncated": summary["total_diffs"] > len(evidence),
            "artifacts": [
                self._artifact("interface_overview_diff_workbook", target),
                self._artifact("interface_overview_diff_evidence", evidence_path),
            ],
            "requires_human_confirmation": (
                summary["total_diffs"] > 0
                or summary["duplicate_new_keys"] > 0
                or summary["duplicate_old_keys"] > 0
                or rename_candidates > 0
            ),
        }
        report_path = output / f"{run_id}_comparison_report.json"
        report["report_path"] = str(report_path.resolve())
        _write_json(report_path, report)
        report["report_artifact"] = self._artifact("comparison_report", report_path)
        return report

    def _compare_source_workbooks(
        self,
        new_path: Path,
        new_sheet: str,
        old_path: Path,
        old_sheet: str,
        target: Path,
        included_modules: list[str] | None,
    ) -> dict:
        """Run the recovered-VBA comparison in-process (pure openpyxl)."""
        included = frozenset(included_modules) if included_modules else None
        stats = compare_source_workbooks(
            new_path,
            new_sheet,
            old_path,
            old_sheet,
            target,
            config=CompareConfig(included_modules=included),
            rename_config=RenameConfig(included_modules=included),
        )
        overview = generate_change_overview(target)
        return {
            "comparison": asdict(stats),
            "change_overview": asdict(overview),
        }

    def _stage_package(self, source: Path, target: Path) -> dict:
        target.mkdir(parents=True, exist_ok=True)
        if source.is_file() and source.suffix.lower() == ".zip":
            try:
                with ZipFile(source) as archive:
                    copied = self._extract_package_members(archive, target)
            except BadZipFile as exc:
                raise ContractError(f"invalid software-interface ZIP: {source}") from exc
            kind = "zip"
        elif source.is_dir():
            root = self._directory_package_root(source)
            inputs = root / "Inputs"
            if not inputs.is_dir():
                raise ContractError(f"package has no Inputs directory: {source}")
            shutil.copytree(inputs, target / "Inputs", dirs_exist_ok=True)
            overview_source = root / "Outputs" / "01_接口收发表"
            if overview_source.is_dir():
                shutil.copytree(
                    overview_source,
                    target / "Outputs" / "01_接口收发表",
                    dirs_exist_ok=True,
                )
            copied = sum(1 for path in target.rglob("*") if path.is_file())
            kind = "directory"
        else:
            raise ContractError("software-interface source must be a ZIP or directory")
        required = [
            target / "Inputs" / "01_PIE_400",
            target / "Inputs" / "02_PIE_Matrix",
            target / "Inputs" / "03_UnitConfig",
        ]
        missing = [str(path.relative_to(target)) for path in required if not path.is_dir()]
        if missing:
            raise ContractError("package is missing required directories: " + ", ".join(missing))
        return {"kind": kind, "staged_files": copied}

    def _extract_package_members(self, archive: ZipFile, target: Path) -> int:
        copied = 0
        for item in archive.infolist():
            if item.is_dir():
                continue
            name = self._decode_zip_name(item).replace("\\", "/")
            relative = self._business_relative(name)
            if relative is None:
                continue
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            copied += 1
        return copied

    @staticmethod
    def _decode_zip_name(item: ZipInfo) -> str:
        if item.flag_bits & 0x800:
            return item.filename
        try:
            return item.filename.encode("cp437").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return item.filename

    @staticmethod
    def _business_relative(name: str) -> PurePosixPath | None:
        parts = PurePosixPath(name).parts
        for marker in ("Inputs", "Outputs"):
            if marker in parts:
                index = parts.index(marker)
                relative = PurePosixPath(*parts[index:])
                if ".." in relative.parts:
                    raise ContractError(f"unsafe ZIP member: {name}")
                if marker == "Inputs":
                    return relative
                if relative.parts[:2] == ("Outputs", "01_接口收发表"):
                    return relative
        return None

    @staticmethod
    def _directory_package_root(source: Path) -> Path:
        if (source / "Inputs").is_dir():
            return source
        candidates = [path for path in source.iterdir() if path.is_dir() and (path / "Inputs").is_dir()]
        if len(candidates) == 1:
            return candidates[0]
        raise ContractError(f"cannot identify package root under {source}")

    @staticmethod
    def _mapping_tool(root: Path) -> Path:
        directory = root / "Inputs" / "03_UnitConfig"
        candidates = [
            path
            for path in directory.glob("*.xlsm")
            if not path.name.startswith("~$") and "mapping" in path.name.casefold()
        ]
        if not candidates:
            candidates = [path for path in directory.glob("*.xlsm") if not path.name.startswith("~$")]
        if not candidates:
            raise ContractError("package has no Mapping XLSM under Inputs/03_UnitConfig")
        return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name.casefold()))

    @staticmethod
    def _select_overview(root: Path) -> Path | None:
        directory = root / "Outputs" / "01_接口收发表"
        candidates = [
            path
            for path in directory.glob(f"{SUMMARY_PREFIX}*.xlsx")
            if path.is_file() and path.name != SUMMARY_NAME
        ]
        if candidates:
            return max(candidates, key=lambda path: path.name.casefold())
        exact = directory / SUMMARY_NAME
        return exact if exact.is_file() else None

    def _select_template(self, root: Path) -> Path:
        exact = root / "Outputs" / "01_接口收发表" / SUMMARY_NAME
        if exact.is_file():
            return exact
        selected = InterfaceDeliveryService._select_overview(root)
        if selected is not None:
            return selected
        if self.default_template.is_file():
            return self.default_template
        raise ContractError(
            "package has no overview template and the controlled default template "
            f"is unavailable: {self.default_template}"
        )

    def _input_manifest(self, root: Path) -> list[dict]:
        rows = []
        for directory in (root / "Inputs", root / "Templates"):
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    rows.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "size_bytes": path.stat().st_size,
                            "sha256": _sha256(path),
                        }
                    )
        return rows

    def _validate_generated_overview(
        self, overview: Path, config: Path, *, max_evidence: int
    ) -> dict:
        rules = self._exclude_rules(config)
        workbook = load_workbook(overview, read_only=True, data_only=True)
        try:
            if SUMMARY_SHEET not in workbook.sheetnames:
                raise ContractError(f"generated overview has no {SUMMARY_SHEET}")
            sheet = workbook[SUMMARY_SHEET]
            header_values = next(
                sheet.iter_rows(min_row=1, max_row=1, values_only=True), ()
            )
            headers = {
                _text(value): column
                for column, value in enumerate(header_values, start=1)
                if _text(value)
            }
            required = (
                "序号",
                "模块名字",
                "Signal方向",
                "Signal接口",
                "信号Mapping",
                "跨应用层SWC链路流",
                "跨应用层SWC信号流",
            )
            missing_headers = [name for name in required if name not in headers]
            if missing_headers:
                raise ContractError(
                    "generated overview is missing headers: " + ", ".join(missing_headers)
                )
            groups: defaultdict[str, list[tuple[int, str, str, str, str, str]]] = defaultdict(list)
            required_blanks: list[dict] = []
            serial_errors: list[dict] = []
            non_participating_mismatches: list[dict] = []
            required_blank_count = 0
            serial_error_count = 0
            non_participating_mismatch_count = 0
            total_rows = 0
            folded_rules = tuple(rule.casefold() for rule in rules)
            def value_at(values: tuple, column: int) -> object:
                return values[column - 1] if column <= len(values) else None

            for row_number, values in enumerate(
                sheet.iter_rows(min_row=2, values_only=True), start=2
            ):
                serial = _text(value_at(values, headers["序号"]))
                module = _text(value_at(values, headers["模块名字"]))
                direction = _text(value_at(values, headers["Signal方向"]))
                signal = _text(value_at(values, headers["Signal接口"]))
                mapping = _text(value_at(values, headers["信号Mapping"]))
                link = _text(value_at(values, headers["跨应用层SWC链路流"]))
                signal_flow = _text(
                    value_at(values, headers["跨应用层SWC信号流"])
                )
                if not any((serial, module, direction, signal, mapping)):
                    continue
                total_rows += 1
                expected_serial = f"{total_rows:05d}"
                if serial.zfill(5) != expected_serial:
                    serial_error_count += 1
                    if len(serial_errors) < max_evidence:
                        serial_errors.append(
                            {"row": row_number, "actual": serial, "expected": expected_serial}
                        )
                blanks = [
                    name
                    for name, value in (
                        ("模块名字", module),
                        ("Signal方向", direction),
                        ("信号Mapping", mapping),
                    )
                    if not value
                ]
                if blanks:
                    required_blank_count += 1
                    if len(required_blanks) < max_evidence:
                        required_blanks.append({"row": row_number, "blank_columns": blanks})
                participates = bool(
                    mapping and any(rule in mapping.casefold() for rule in folded_rules)
                )
                if participates:
                    groups[mapping].append(
                        (row_number, module, direction, signal, link, signal_flow)
                    )
                elif link or signal_flow:
                    non_participating_mismatch_count += 1
                    if len(non_participating_mismatches) < max_evidence:
                        non_participating_mismatches.append(
                            {
                                "row": row_number,
                                "mapping": mapping,
                                "actual": {"AA": link, "AB": signal_flow},
                                "expected": {"AA": "", "AB": ""},
                            }
                        )

            mismatches: list[dict] = []
            mismatch_count = 0
            valid_rows = 0
            missing_rows = 0
            participating_rows = 0
            for mapping, members in groups.items():
                filtered = [item for item in members if item[1] not in ADAPTER_MODULES]
                outputs = [item for item in filtered if item[2] == "Output"]
                inputs = [item for item in filtered if item[2] == "Input"]
                if len(outputs) == 1 and inputs:
                    output_row = outputs[0]
                    input_modules = list(dict.fromkeys(item[1] for item in inputs))
                    input_signals = list(dict.fromkeys(item[3] for item in inputs))
                    expected_link = output_row[1] + "→" + ",".join(input_modules)
                    expected_signal = output_row[3] + "→" + ",".join(input_signals)
                    valid_rows += len(members)
                else:
                    expected_link = MISSING_FLOW_TEXT
                    expected_signal = MISSING_FLOW_TEXT
                    missing_rows += len(members)
                participating_rows += len(members)
                for row_number, module, direction, signal, actual_link, actual_signal in members:
                    if actual_link != expected_link or actual_signal != expected_signal:
                        mismatch_count += 1
                        if len(mismatches) < max_evidence:
                            mismatches.append(
                                {
                                    "row": row_number,
                                    "mapping": mapping,
                                    "module": module,
                                    "direction": direction,
                                    "signal": signal,
                                    "actual": {"AA": actual_link, "AB": actual_signal},
                                    "expected": {"AA": expected_link, "AB": expected_signal},
                                }
                            )
            summary = {
                "data_rows": total_rows,
                "exclude_rules": len(rules),
                "participating_groups": len(groups),
                "participating_rows": participating_rows,
                "valid_link_rows": valid_rows,
                "missing_link_rows": missing_rows,
                "non_participating_rows": total_rows - participating_rows,
                "serial_errors": serial_error_count,
                "required_blank_rows": required_blank_count,
                "aa_ab_mismatches": mismatch_count,
                "non_participating_aa_ab_mismatches": non_participating_mismatch_count,
            }
            return {
                "passed": not any(
                    (
                        serial_error_count,
                        required_blank_count,
                        mismatch_count,
                        non_participating_mismatch_count,
                    )
                ),
                "summary": summary,
                "evidence": {
                    "serial_errors": serial_errors,
                    "required_blank_rows": required_blanks,
                    "aa_ab_mismatches": mismatches,
                    "non_participating_aa_ab_mismatches": non_participating_mismatches,
                },
                "rules": {
                    "adapter_modules": sorted(ADAPTER_MODULES),
                    "valid_link": "exactly one non-adapter Output and at least one non-adapter Input",
                    "non_participating": "AA/AB must remain blank when no exclude-rule prefix matches",
                },
            }
        finally:
            workbook.close()

    @staticmethod
    def _exclude_rules(config: Path) -> list[str]:
        workbook = load_workbook(config, read_only=True, data_only=True)
        try:
            if EXTERNAL_CONFIG_SHEET not in workbook.sheetnames:
                raise ContractError(f"configuration has no {EXTERNAL_CONFIG_SHEET}")
            sheet = workbook[EXTERNAL_CONFIG_SHEET]
            rules = []
            for key, result, *_ in sheet.iter_rows(
                min_row=2, min_col=1, max_col=2, values_only=True
            ):
                normalized = (
                    str(result or "")
                    .replace("，", ",")
                    .replace(" ", "")
                    .replace("\t", "")
                )
                rule = _text(key)
                if rule and normalized == EXCLUDE_RESULT and rule not in rules:
                    rules.append(rule)
            return rules
        finally:
            workbook.close()

    @staticmethod
    def _content_multiset_parity(
        new_path: Path,
        old_path: Path,
        *,
        new_sheet: str,
        old_sheet: str,
        max_evidence: int,
    ) -> dict:
        """Compare exact row content as a multiset, excluding serial numbers.

        The recovered comparator intentionally pairs rows by a non-unique
        business key.  When duplicate keys exist, order changes can therefore
        appear as content changes.  This second, read-only view does not replace
        the production comparison; it exposes whether any exact legacy row was
        truly lost and makes migration evidence unambiguous.
        """

        def rows(path: Path, sheet_name: str) -> tuple[tuple[str, ...], Counter]:
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                if sheet_name not in workbook.sheetnames:
                    raise ContractError(f"workbook has no {sheet_name}: {path}")
                sheet = workbook[sheet_name]
                raw_headers = next(
                    sheet.iter_rows(min_row=1, max_row=1, values_only=True), ()
                )
                headers = tuple(_text(value) for value in raw_headers)
                serial_index = (
                    headers.index("序号") if "序号" in headers else None
                )
                kept_headers = tuple(
                    value
                    for index, value in enumerate(headers)
                    if index != serial_index
                )
                counter: Counter[tuple[str, ...]] = Counter()
                for raw in sheet.iter_rows(min_row=2, values_only=True):
                    padded = tuple(raw) + (None,) * max(0, len(headers) - len(raw))
                    canonical = tuple(
                        _text(value)
                        for index, value in enumerate(padded[: len(headers)])
                        if index != serial_index
                    )
                    if any(canonical):
                        counter[canonical] += 1
                return kept_headers, counter
            finally:
                workbook.close()

        new_headers, new_rows = rows(new_path, new_sheet)
        old_headers, old_rows = rows(old_path, old_sheet)
        if new_headers != old_headers:
            return {
                "comparable": False,
                "reason": "headers differ after excluding 序号",
                "new_headers": list(new_headers),
                "old_headers": list(old_headers),
            }
        common = new_rows & old_rows
        new_only = new_rows - old_rows
        old_only = old_rows - new_rows

        def examples(counter: Counter) -> list[dict]:
            result = []
            for values in counter.elements():
                row = dict(zip(new_headers, values))
                result.append(
                    {
                        "module": row.get("模块名字", ""),
                        "direction": row.get("Signal方向", ""),
                        "method": row.get("传参方式", ""),
                        "signal": row.get("Signal接口", ""),
                        "mapping": row.get("信号Mapping", ""),
                    }
                )
                if len(result) >= max_evidence:
                    break
            return result

        return {
            "comparable": True,
            "serial_column_excluded": True,
            "new_rows": sum(new_rows.values()),
            "old_rows": sum(old_rows.values()),
            "exact_common_rows": sum(common.values()),
            "new_only_rows": sum(new_only.values()),
            "old_only_rows": sum(old_only.values()),
            "all_old_rows_preserved": not old_only,
            "identical": not new_only and not old_only,
            "new_only_examples": examples(new_only),
            "old_only_examples": examples(old_only),
            "evidence_truncated": (
                sum(new_only.values()) > max_evidence
                or sum(old_only.values()) > max_evidence
            ),
        }

    @staticmethod
    def _comparison_evidence(
        workbook_path: Path, *, max_evidence: int
    ) -> tuple[list[dict], dict[str, int], int]:
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
        try:
            if "对比结果" not in workbook.sheetnames:
                raise ContractError("comparison output has no 对比结果 sheet")
            sheet = workbook["对比结果"]
            header_values = next(
                sheet.iter_rows(min_row=1, max_row=1, values_only=True), ()
            )
            headers = {
                _text(value): column
                for column, value in enumerate(header_values, start=1)
                if _text(value)
            }
            status_col = headers.get("差异状态")
            if status_col is None:
                raise ContractError("comparison output has no 差异状态 column")
            module_col = headers.get("模块名字", 2 if headers.get("序号") == 1 else 1)
            signal_col = headers.get("Signal接口", 9 if headers.get("序号") == 1 else 8)
            mapping_col = headers.get("信号Mapping")
            record_col = headers.get("变更记录")
            evidence: list[dict] = []
            counts: Counter[str] = Counter()
            def value_at(values: tuple, column: int | None) -> object:
                if column is None or column > len(values):
                    return None
                return values[column - 1]

            for row_number, values in enumerate(
                sheet.iter_rows(min_row=2, values_only=True), start=2
            ):
                status = _text(value_at(values, status_col))
                if not status or status == "无差异":
                    continue
                counts[status] += 1
                if len(evidence) < max_evidence:
                    evidence.append(
                        {
                            "row": row_number,
                            "status": status,
                            "module": _text(value_at(values, module_col)),
                            "signal": _text(value_at(values, signal_col)),
                            "mapping": _text(value_at(values, mapping_col)),
                            "change_record": _text(value_at(values, record_col)),
                        }
                    )
            rename_candidates = (
                max(0, workbook["疑似重命名"].max_row - 1)
                if "疑似重命名" in workbook.sheetnames
                else 0
            )
            return evidence, dict(sorted(counts.items())), rename_candidates
        finally:
            workbook.close()

    @staticmethod
    def _artifact(kind: str, path: Path) -> dict:
        return {
            "type": kind,
            "path": str(path.resolve()),
            "sha256": _sha256(path),
        }

    @staticmethod
    def _unique_target(directory: Path, name: str) -> Path:
        candidate = directory / name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        for index in range(2, 10000):
            numbered = candidate.with_name(f"{stem}_{index}{candidate.suffix}")
            if not numbered.exists():
                return numbered
        raise ContractError(f"cannot allocate a unique output filename for {name}")

    @staticmethod
    def _source_fingerprint(source: Path) -> str:
        if source.is_file():
            return _sha256(source)
        digest = hashlib.sha256()
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_sha256(path).encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()


# Kept for existing callers and serialized integrations created before the
# pure-Python generation engine was introduced.
LegacyInterfaceDeliveryService = InterfaceDeliveryService
