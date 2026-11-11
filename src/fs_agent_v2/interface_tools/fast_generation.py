from __future__ import annotations

from collections import OrderedDict, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from time import perf_counter
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zipfile import ZipFile
import posixpath
import re

from openpyxl import load_workbook

from ..errors import ContractError


SUMMARY_NAME = "软件组件接口收发总览表.xlsx"
MISSING_FLOW_TEXT = "链路流信号流缺失"
ADAPTER_MODULES = frozenset(
    {
        "ACF-ACFService",
        "FM-FMService",
        "MCF-MCFService",
        "RCF-RCFService",
        "FMB-FMBService",
    }
)
INTERNAL_SHEET = "内部单元和组件映射配置"
EXTERNAL_SHEET = "外部组件映射配置"
SIGNAL_SHEET = "信号和参数定义"
MATRIX_SHEETS = OrderedDict(
    (
        ("CtApFuncManager", "FM-FMService"),
        ("CtApMainCtlFunc", "MCF-MCFService"),
        ("CtApRelaCtlFunc", "RCF-RCFService"),
        ("CtApAsilCtlFunc", "ACF-ACFService"),
        ("CtApFuncManagerB", "FMB-FMBService"),
    )
)
SWC_TO_COMPONENT = {
    "FuncManager": "FM-FMService",
    "AsilCtlFunc": "ACF-ACFService",
    "MainCtlFunc": "MCF-MCFService",
    "RelaCtlFunc": "RCF-RCFService",
    "FuncManagerB": "FMB-FMBService",
}
HEADERS = (
    "序号",
    "模块名字",
    "Signal方向",
    "传参方式",
    "Signal上下游方",
    "PP接口/Topic接口",
    "DataType接口（一级）",
    "DataType接口（二级）",
    "Signal接口",
    "Signal中文含义",
    "数据类型",
    "coding取值",
    "维度",
    "所属Runnable",
    "信号Mapping",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "跨应用层SWC链路流",
    "跨应用层SWC信号流",
)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _ordered_add(target: OrderedDict[str, None], value: str) -> None:
    if value and value not in target:
        target[value] = None


@dataclass(frozen=True)
class FastGenerationStats:
    module_files: int
    module_rows: int
    retained_component_rows: int
    matrix_rows: int
    rte_rows: int
    overview_rows: int
    participating_groups: int
    participating_rows: int
    valid_link_rows: int
    missing_link_rows: int
    duration_seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _ModuleRow:
    values: list[str]
    filename: str
    component: str = "未匹配"
    swc: str = "未匹配"
    adapter: str = "未匹配"
    relation: str = ""

    @property
    def name(self) -> str:
        return self.values[0]

    @property
    def direction(self) -> str:
        return self.values[2]

    @property
    def mapping(self) -> str:
        return "|".join((self.values[12], self.values[10], self.values[11]))


@dataclass
class _MatrixRow:
    values: list[str]
    filenames: str
    units: str = ""
    components: str = "未匹配"
    swcs: str = "未匹配"
    adapter: str = "未匹配"
    relation: str = ""
    outer_type: str = ""
    outer_component: str = ""
    outer_relation: str = ""

    @property
    def direction(self) -> str:
        return self.values[2]

    @property
    def mapping(self) -> str:
        return "|".join((self.values[12], self.values[10], self.values[11]))


class FastInterfaceOverviewGenerator:
    """Pure-Python equivalent of the recovered overview-generation VBA."""

    def generate(self, root: Path | str) -> tuple[Path, dict]:
        started = perf_counter()
        phase_started = started
        phase_seconds: OrderedDict[str, float] = OrderedDict()
        package = Path(root).resolve(strict=True)
        modules_dir = package / "Inputs" / "01_PIE_400"
        matrix_dir = package / "Inputs" / "02_PIE_Matrix"
        config_path = package / "Inputs" / "03_UnitConfig" / "Unit2ComponentConfig.xlsx"
        template_path = package / "Templates" / SUMMARY_NAME
        for required in (modules_dir, matrix_dir, config_path, template_path):
            if not required.exists():
                raise ContractError(f"fast generation input is missing: {required}")

        module_files = [
            path
            for path in modules_dir.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".xlsm"
            and not path.name.startswith("~")
        ]
        if not module_files:
            raise ContractError("fast generation found no module XLSM files")
        config = self._update_and_load_config(config_path, module_files)
        phase_seconds["load_and_update_config"] = round(
            perf_counter() - phase_started, 3
        )
        phase_started = perf_counter()
        # Newly staged files pay a per-file cold-open cost (real-time AV scan,
        # cold cache), so production always reads them cold; worker processes
        # overlap that cost, which a thread pool cannot (GIL-bound parsing).
        workers = min(8, max(1, len(module_files)))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            batches = list(pool.map(self._read_module, module_files))
        module_rows = [item for batch in batches for item in batch]
        phase_seconds["read_module_workbooks"] = round(
            perf_counter() - phase_started, 3
        )
        phase_started = perf_counter()
        for item in module_rows:
            mapped = config["by_file"].get(item.filename.casefold())
            if mapped:
                item.component = mapped["component"] or "未匹配"
                item.swc = mapped["swc"] or "未匹配"
                item.adapter = SWC_TO_COMPONENT.get(item.swc, "未匹配")

        component_rows = self._component_rows(module_rows)
        matrix_path = self._latest_matrix(matrix_dir)
        matrix_rows = self._matrix_rows(matrix_path, config)
        phase_seconds["build_component_and_matrix_rows"] = round(
            perf_counter() - phase_started, 3
        )
        phase_started = perf_counter()
        overview_rows, rte_rows = self._unified_rows(component_rows, matrix_rows)
        flow_stats = self._populate_flows(overview_rows, config["exclude_keys"])
        phase_seconds["build_overview_and_flows"] = round(
            perf_counter() - phase_started, 3
        )
        phase_started = perf_counter()
        output_dir = package / "Outputs" / "01_接口收发表"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"软件组件接口收发总览表_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        self._write_overview(template_path, output_path, overview_rows)
        phase_seconds["write_overview_workbook"] = round(
            perf_counter() - phase_started, 3
        )
        stats = FastGenerationStats(
            module_files=len(module_files),
            module_rows=len(module_rows),
            retained_component_rows=len(component_rows),
            matrix_rows=len(matrix_rows),
            rte_rows=rte_rows,
            overview_rows=len(overview_rows),
            participating_groups=flow_stats["groups"],
            participating_rows=flow_stats["participating_rows"],
            valid_link_rows=flow_stats["valid_rows"],
            missing_link_rows=flow_stats["missing_rows"],
            duration_seconds=round(perf_counter() - started, 3),
        )
        details = stats.to_dict()
        details["output_strategy"] = "xlsx_sheet_xml_replacement"
        details["module_read_strategy"] = "process_pool_ooxml_xml"
        details["module_read_workers"] = workers
        details["phase_seconds"] = phase_seconds
        return output_path, details

    def _update_and_load_config(self, path: Path, module_files: list[Path]) -> dict:
        workbook = load_workbook(path)
        try:
            if INTERNAL_SHEET not in workbook.sheetnames or EXTERNAL_SHEET not in workbook.sheetnames:
                raise ContractError("Unit config is missing internal/external mapping sheets")
            internal = workbook[INTERNAL_SHEET]
            while internal.max_row >= 2:
                row = internal.max_row
                a, b, c = (_text(internal.cell(row, col).value) for col in (1, 2, 3))
                if not a and not b and (c.startswith("未匹配") or c.startswith("疑似匹配")):
                    internal.delete_rows(row)
                else:
                    break
            formal_last = internal.max_row
            units: OrderedDict[str, int] = OrderedDict()
            for row in range(2, formal_last + 1):
                unit = _text(internal.cell(row, 3).value)
                if unit and unit not in units:
                    units[unit] = row
                internal.cell(row, 4).value = None
            unmatched: list[str] = []
            for module in module_files:
                matches = [
                    unit for unit in units if module.name.casefold().startswith(unit.casefold())
                ]
                match = max(matches, key=len) if matches else ""
                if not match:
                    prefix = self._file_prefix(module.name)
                    match = next((unit for unit in units if unit.casefold() == prefix.casefold()), "")
                if match and internal.cell(units[match], 4).value in (None, ""):
                    internal.cell(units[match], 4).value = module.name
                else:
                    unmatched.append(module.name)
            for name in unmatched:
                internal.append(["", "", "未匹配", name, "", ""])
            for row in range(2, formal_last + 1):
                internal.cell(row, 6).value = (
                    "已匹配" if _text(internal.cell(row, 4).value) else None
                )
            workbook.save(path)

            by_file: dict[str, dict[str, str]] = {}
            for row in range(2, internal.max_row + 1):
                filename = _text(internal.cell(row, 4).value)
                if filename and filename.casefold() not in by_file:
                    by_file[filename.casefold()] = {
                        "unit": _text(internal.cell(row, 3).value),
                        "component": _text(internal.cell(row, 2).value),
                        "swc": _text(internal.cell(row, 1).value),
                    }
            external = workbook[EXTERNAL_SHEET]
            rules: list[tuple[str, str, str, str]] = []
            exclude_keys: list[str] = []
            for row in external.iter_rows(min_row=2, min_col=1, max_col=4, values_only=True):
                key, value, link, relation = map(_text, row)
                if not key or not value:
                    continue
                rules.append((key, value, link, relation))
                normalized = value.replace("，", ",").replace(" ", "")
                if normalized == "剔除,不做任何处理":
                    exclude_keys.append(key)
            rules.sort(key=lambda item: len(item[0]), reverse=True)
            return {"by_file": by_file, "rules": rules, "exclude_keys": exclude_keys}
        finally:
            workbook.close()

    @staticmethod
    def _file_prefix(filename: str) -> str:
        parts = Path(filename).stem.split("_")
        if len(parts) >= 2 and len(parts[-1]) == 8 and parts[-1].isdigit():
            parts.pop()
        if len(parts) >= 2 and re.fullmatch(r"SW\d{3}", parts[-1]):
            parts.pop()
        return "_".join(parts)

    @staticmethod
    def _read_module(path: Path) -> list[_ModuleRow]:
        with ZipFile(path) as archive:
            if SIGNAL_SHEET not in FastInterfaceOverviewGenerator._sheet_names(archive):
                return []
            source_rows: list[list[str]] = []
            for values in FastInterfaceOverviewGenerator._sheet_rows(
                archive, SIGNAL_SHEET, min_row=9, max_col=14
            ):
                source_rows.append([_text(value) for value in values])
        last_name_row = max(
            (index for index, values in enumerate(source_rows) if values[0]),
            default=-1,
        )
        return [
            _ModuleRow(values, path.name)
            for values in source_rows[: last_name_row + 1]
            if any(values)
        ]

    @staticmethod
    def _merge_relation(current: str, new: str) -> str:
        if not current:
            return new
        old_parts = current.split("→")
        new_parts = new.split("→")
        if len(old_parts) == 2 and len(new_parts) == 2 and old_parts[0].strip() == new_parts[0].strip():
            return old_parts[0].strip() + "→" + old_parts[1].strip() + "," + new_parts[1].strip()
        return current + "," + new

    def _component_rows(self, rows: list[_ModuleRow]) -> list[_ModuleRow]:
        senders: dict[str, str] = {}
        receivers: defaultdict[str, OrderedDict[str, None]] = defaultdict(OrderedDict)
        for item in rows:
            if item.name and item.component:
                if item.direction == "Output":
                    senders[item.name] = item.component
                elif item.direction == "Input":
                    _ordered_add(receivers[item.name], item.component)
        kept: list[_ModuleRow] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in rows:
            if item.direction == "Input" and item.name in senders:
                item.relation = senders[item.name] + "→" + item.component
            elif item.direction == "Output" and receivers.get(item.name):
                item.relation = item.component + "→" + ",".join(receivers[item.name])
            if item.mapping and item.mapping != "||":
                if item.direction == "Input":
                    new = item.adapter + "→" + item.component
                elif item.direction == "Output":
                    new = item.component + "→" + item.adapter
                else:
                    new = ""
                if new:
                    item.relation = self._merge_relation(item.relation, new)
            if not item.relation:
                continue
            key = (item.name, item.direction, item.mapping, item.component)
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        return kept

    @staticmethod
    def _latest_matrix(directory: Path) -> Path:
        candidates = [
            path
            for path in directory.glob("PIE_Matrix_Res_*.xlsx")
            if not path.name.startswith("~$")
        ]
        if not candidates:
            raise ContractError("PIE Matrix workbook is missing")
        def stamp(path: Path) -> str:
            match = re.search(r"(\d{8})(?!.*\d)", path.stem)
            return match.group(1) if match else ""
        return max(candidates, key=lambda path: (stamp(path), path.name.casefold()))

    def _matrix_rows(self, path: Path, config: dict) -> list[_MatrixRow]:
        output: list[_MatrixRow] = []
        with ZipFile(path) as archive:
            sheet_names = FastInterfaceOverviewGenerator._sheet_names(archive)
            for sheet_name in MATRIX_SHEETS:
                if sheet_name not in sheet_names:
                    continue
                for row in FastInterfaceOverviewGenerator._sheet_rows(
                    archive, sheet_name, min_row=2, max_col=43
                ):
                    values = list(row) + [None] * (43 - len(row))
                    if _text(values[11]):
                        copied = [_text(value) for value in values[12:26]]
                        output.append(_MatrixRow(copied, _text(values[11])))
                    if _text(values[28]):
                        copied = [_text(value) for value in values[29:43]]
                        output.append(_MatrixRow(copied, _text(values[28])))

        by_file = config["by_file"]
        for item in output:
            units: OrderedDict[str, None] = OrderedDict()
            components: OrderedDict[str, None] = OrderedDict()
            swcs: OrderedDict[str, None] = OrderedDict()
            adapters: OrderedDict[str, None] = OrderedDict()
            for raw_name in item.filenames.split(",") if item.filenames else []:
                mapped = by_file.get(raw_name.strip().casefold())
                if not mapped:
                    continue
                _ordered_add(units, mapped["unit"])
                _ordered_add(components, mapped["component"])
                _ordered_add(swcs, mapped["swc"])
                _ordered_add(adapters, SWC_TO_COMPONENT.get(mapped["swc"], ""))
            if item.filenames:
                item.units = ",".join(units)
                item.components = ",".join(components) or "未匹配"
                item.swcs = ",".join(swcs) or "未匹配"
                item.adapter = ",".join(adapters) or "未匹配"
            if item.direction == "Input":
                item.relation = item.adapter + "→" + item.components
            elif item.direction == "Output":
                item.relation = item.components + "→" + item.adapter
            self._map_external(item, config["rules"])
        return output

    @staticmethod
    def _map_external(item: _MatrixRow, rules: list[tuple[str, str, str, str]]) -> None:
        mapping = item.mapping
        if not mapping or mapping == "||":
            return
        interface = mapping.split("|", 1)[0].strip()
        folded = interface.casefold()
        for key, value, _link, relation in rules:
            normalized = value.replace("，", ",").replace(" ", "")
            if normalized == "剔除,不做任何处理" and folded.startswith(key.casefold()):
                item.outer_relation = relation
                return
        matched_value = ""
        matched_link = ""
        if re.search(r"_PDU\d{2,3}", mapping, re.IGNORECASE) and "Dt_RECORD_MeasuredValue_Adapter" not in mapping:
            matched_value = "APCOM"
            matched_link = next((link for key, _value, link, _relation in rules if key == "_PDU"), "")
        else:
            for key, value, link, _relation in rules:
                if value in {"EthCOM", "MiddleWare"} and folded.startswith(key.casefold()):
                    matched_value, matched_link = value, link
                    break
        item.outer_type = matched_value or "MiddleWare"
        item.outer_component = matched_link or "(Mw)OtherSWC"
        if item.outer_type and item.outer_component and item.adapter:
            if item.direction == "Input":
                item.outer_relation = item.outer_component + "→" + item.adapter
            elif item.direction == "Output":
                item.outer_relation = item.adapter + "→" + item.outer_component

    @staticmethod
    def _receiver(relation: str, direction: str) -> str:
        if "→" not in relation:
            return "未匹配"
        parts = relation.split("→")
        if direction == "Input":
            return parts[0].strip()
        if direction == "Output" and len(parts) > 1:
            return parts[1].strip()
        return "未匹配"

    def _unified_rows(
        self, component_rows: list[_ModuleRow], matrix_rows: list[_MatrixRow]
    ) -> tuple[list[list[str]], int]:
        result: list[list[str]] = []
        grouped: defaultdict[str, list[_ModuleRow]] = defaultdict(list)
        for item in component_rows:
            grouped[item.component].append(item)
        for component in sorted(grouped):
            for item in sorted(grouped[component], key=lambda value: value.direction):
                result.append(
                    [
                        component,
                        item.direction,
                        "GV",
                        self._receiver(item.relation, item.direction),
                        "/",
                        "/",
                        "/",
                        item.values[0],
                        item.values[6],
                        item.values[3],
                        item.values[13],
                        item.values[7],
                        "",
                        item.mapping,
                    ]
                )
        t_values: OrderedDict[str, None] = OrderedDict()
        v_values: OrderedDict[str, None] = OrderedDict()
        for item in matrix_rows:
            for value in item.adapter.split(","):
                if value and value != "未匹配":
                    _ordered_add(t_values, value.strip())
            _ordered_add(v_values, item.outer_type)
        rte_rows = 0
        for key in t_values:
            members = [item for item in matrix_rows if key in {v.strip() for v in item.adapter.split(",")}]
            for item in members:
                result.append(self._matrix_unified(item, key, item.direction, "RTE", item.outer_relation))
                rte_rows += 1
            for item in members:
                direction = "Output" if item.direction == "Input" else "Input" if item.direction == "Output" else item.direction
                result.append(self._matrix_unified(item, key, direction, "GV", item.relation))
        for key in v_values:
            if not key:
                continue
            for item in matrix_rows:
                if item.outer_type == key:
                    # The recovered VBA builds each external ``*_Outer`` sheet by
                    # copying the matrix row and then swapping Input/Output.  X is
                    # intentionally left unchanged, so receiver extraction must
                    # use the swapped (external-component) point of view too.
                    direction = (
                        "Output"
                        if item.direction == "Input"
                        else "Input"
                        if item.direction == "Output"
                        else item.direction
                    )
                    result.append(
                        self._matrix_unified(
                            item,
                            item.outer_component,
                            direction,
                            "RTE",
                            item.outer_relation,
                        )
                    )
                    rte_rows += 1
        return result, rte_rows

    def _matrix_unified(
        self,
        item: _MatrixRow,
        module: str,
        direction: str,
        method: str,
        relation: str,
    ) -> list[str]:
        return [
            module,
            direction,
            method,
            self._receiver(relation, direction),
            "/" if method == "GV" else item.values[12],
            "/" if method == "GV" else item.values[10],
            "/",
            item.values[11] if method == "RTE" else item.values[0],
            item.values[6],
            item.values[3],
            item.values[13],
            item.values[7],
            "",
            item.mapping,
        ]

    @staticmethod
    def _populate_flows(rows: list[list[str]], exclude_keys: list[str]) -> dict:
        groups: defaultdict[str, list[int]] = defaultdict(list)
        folded = tuple(key.casefold() for key in exclude_keys)
        for index, row in enumerate(rows):
            mapping = row[13]
            if mapping and any(key in mapping.casefold() for key in folded):
                groups[mapping].append(index)
        valid_rows = missing_rows = 0
        for members in groups.values():
            filtered = [index for index in members if rows[index][0] not in ADAPTER_MODULES]
            outputs = [index for index in filtered if rows[index][1] == "Output"]
            inputs = [index for index in filtered if rows[index][1] == "Input"]
            if len(outputs) == 1 and inputs:
                output = rows[outputs[0]]
                modules = list(OrderedDict((rows[index][0], None) for index in inputs))
                signals = list(OrderedDict((rows[index][7], None) for index in inputs))
                link = output[0] + "→" + ",".join(modules)
                signal = output[7] + "→" + ",".join(signals)
                valid_rows += len(members)
            else:
                link = signal = MISSING_FLOW_TEXT
                missing_rows += len(members)
            for index in members:
                rows[index].extend([""] * (27 - len(rows[index])))
                rows[index][25] = link
                rows[index][26] = signal
        for row in rows:
            row.extend([""] * (27 - len(row)))
        return {
            "groups": len(groups),
            "participating_rows": sum(map(len, groups.values())),
            "valid_rows": valid_rows,
            "missing_rows": missing_rows,
        }

    @staticmethod
    def _write_overview(template: Path, output: Path, rows: list[list[str]]) -> None:
        """Replace only Sheet1 data inside the XLSX package.

        Loading the completed legacy workbook with openpyxl materializes all of
        its 29 worksheets and saving it serializes them all again.  Only Sheet1
        changes during fast generation, so copying the OOXML package and
        replacing that one worksheet preserves every unrelated workbook part
        while avoiding millions of unnecessary Cell objects.
        """
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                prefix=f".{output.name}.",
                suffix=".tmp",
                dir=output.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)

            with ZipFile(template, "r") as source:
                worksheet_part = FastInterfaceOverviewGenerator._worksheet_part(
                    source, "Sheet1"
                )
                original_sheet = source.read(worksheet_part)
                replacement_sheet = (
                    FastInterfaceOverviewGenerator._replace_sheet_data(
                        original_sheet, rows
                    )
                )
                with ZipFile(temporary, "w", allowZip64=True) as target:
                    target.comment = source.comment
                    for member in source.infolist():
                        payload = (
                            replacement_sheet
                            if member.filename == worksheet_part
                            else source.read(member.filename)
                        )
                        target.writestr(member, payload)
            temporary.replace(output)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _local_tag(element: ElementTree.Element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    @staticmethod
    def _sheet_names(workbook: ZipFile) -> list[str]:
        root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        return [
            element.attrib.get("name", "")
            for element in root.iter()
            if FastInterfaceOverviewGenerator._local_tag(element) == "sheet"
        ]

    @staticmethod
    def _shared_strings(workbook: ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in workbook.namelist():
            return []
        root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
        values: list[str] = []
        for item in root:
            if FastInterfaceOverviewGenerator._local_tag(item) != "si":
                continue
            values.append(
                "".join(
                    node.text or ""
                    for node in item.iter()
                    if FastInterfaceOverviewGenerator._local_tag(node) == "t"
                )
            )
        return values

    @staticmethod
    def _column_index(reference: str) -> int:
        column = 0
        for character in reference:
            if not character.isalpha():
                break
            column = column * 26 + (ord(character.upper()) - 64)
        return column

    @staticmethod
    def _cell_value(cell: ElementTree.Element, strings: list[str]) -> object:
        """Read one ``<c>`` element with openpyxl ``data_only`` semantics."""
        cell_type = cell.attrib.get("t", "")
        if cell_type == "inlineStr":
            return "".join(
                node.text or ""
                for node in cell.iter()
                if FastInterfaceOverviewGenerator._local_tag(node) == "t"
            )
        raw = ""
        for node in cell:
            if FastInterfaceOverviewGenerator._local_tag(node) == "v":
                raw = node.text or ""
                break
        if cell_type == "s":
            return strings[int(raw)]
        if cell_type == "str":
            return raw
        if cell_type == "b":
            return raw not in ("", "0")
        if cell_type == "e":
            return raw
        # Numeric cells follow openpyxl's number cast: int without decimal
        # point or exponent, float otherwise.
        if "." in raw or "E" in raw or "e" in raw:
            return float(raw)
        return int(raw) if raw else None

    @staticmethod
    def _sheet_rows(
        workbook: ZipFile, sheet_name: str, *, min_row: int, max_col: int
    ) -> list[list[object]]:
        """Parse a worksheet directly from OOXML, bypassing openpyxl.

        Produces the same values ``load_workbook(read_only=True,
        data_only=True).iter_rows(min_row, max_col, values_only=True)`` would
        return, but without materializing Cell objects.
        """
        part = FastInterfaceOverviewGenerator._worksheet_part(workbook, sheet_name)
        strings = FastInterfaceOverviewGenerator._shared_strings(workbook)
        root = ElementTree.fromstring(workbook.read(part))
        rows: list[list[object]] = []
        for element in root.iter():
            if FastInterfaceOverviewGenerator._local_tag(element) != "row":
                continue
            if int(element.attrib.get("r", "0") or 0) < min_row:
                continue
            values: list[object] = [None] * max_col
            expected = 1
            for cell in element:
                if FastInterfaceOverviewGenerator._local_tag(cell) != "c":
                    continue
                reference = cell.attrib.get("r")
                column = (
                    FastInterfaceOverviewGenerator._column_index(reference)
                    if reference
                    else expected
                )
                expected = column + 1
                if column > max_col:
                    continue
                values[column - 1] = FastInterfaceOverviewGenerator._cell_value(
                    cell, strings
                )
            rows.append(values)
        return rows

    @staticmethod
    def _worksheet_part(workbook: ZipFile, sheet_name: str) -> str:
        workbook_xml = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        relationship_id = ""
        for element in workbook_xml.iter():
            if element.tag.rsplit("}", 1)[-1] != "sheet":
                continue
            if element.attrib.get("name") != sheet_name:
                continue
            relationship_id = next(
                (
                    value
                    for key, value in element.attrib.items()
                    if key.rsplit("}", 1)[-1] == "id"
                ),
                "",
            )
            break
        if not relationship_id:
            raise ContractError(f"overview template has no {sheet_name}")

        relationships = ElementTree.fromstring(
            workbook.read("xl/_rels/workbook.xml.rels")
        )
        target_name = ""
        for element in relationships.iter():
            if element.tag.rsplit("}", 1)[-1] != "Relationship":
                continue
            if element.attrib.get("Id") == relationship_id:
                target_name = element.attrib.get("Target", "")
                break
        if not target_name:
            raise ContractError(
                f"overview template relationship is missing for {sheet_name}"
            )
        if target_name.startswith("/"):
            part = target_name.lstrip("/")
        else:
            part = posixpath.normpath(
                str(PurePosixPath("xl") / PurePosixPath(target_name))
            )
        if part not in workbook.namelist():
            raise ContractError(f"overview template worksheet is missing: {part}")
        return part

    @staticmethod
    def _replace_sheet_data(sheet_xml: bytes, rows: list[list[str]]) -> bytes:
        start = sheet_xml.find(b"<sheetData")
        close = b"</sheetData>"
        if start < 0:
            raise ContractError("overview template Sheet1 has no sheetData")
        opening_end = sheet_xml.find(b">", start)
        end = sheet_xml.find(close, opening_end)
        if opening_end < 0 or end < 0:
            raise ContractError("overview template Sheet1 sheetData is malformed")
        end += len(close)

        header_style = FastInterfaceOverviewGenerator._first_row_style(
            sheet_xml[opening_end + 1 : end - len(close)]
        )
        sheet_data = FastInterfaceOverviewGenerator._sheet_data_xml(
            rows, header_style
        )
        updated = sheet_xml[:start] + sheet_data + sheet_xml[end:]
        dimension = f'A1:AB{len(rows) + 1}'.encode("ascii")
        dimension_pattern = re.compile(rb'(<dimension\b[^>]*\bref=")[^"]*(")')
        if dimension_pattern.search(updated):
            updated = dimension_pattern.sub(
                lambda match: match.group(1) + dimension + match.group(2),
                updated,
                count=1,
            )
        return updated

    @staticmethod
    def _first_row_style(sheet_data: bytes) -> str:
        row = re.search(rb'<row\b[^>]*\br="1"[^>]*>.*?</row>', sheet_data, re.DOTALL)
        if not row:
            return ""
        for cell in re.finditer(rb"<c\b([^>]*)>", row.group(0)):
            style = re.search(rb'\bs="(\d+)"', cell.group(1))
            if style:
                return style.group(1).decode("ascii")
        return ""

    @staticmethod
    def _sheet_data_xml(rows: list[list[str]], header_style: str) -> bytes:
        parts = ["<sheetData><row r=\"1\" spans=\"1:28\">"]
        style = f' s="{header_style}"' if header_style else ""
        for column, value in enumerate(HEADERS, start=1):
            reference = f"{FastInterfaceOverviewGenerator._column_name(column)}1"
            if value:
                parts.append(
                    f'<c r="{reference}"{style} t="inlineStr"><is><t '
                    f'xml:space="preserve">{FastInterfaceOverviewGenerator._xml_text(value)}</t>'
                    "</is></c>"
                )
            else:
                parts.append(f'<c r="{reference}"{style}/>' )
        parts.append("</row>")

        for serial, values in enumerate(rows, start=1):
            excel_row = serial + 1
            parts.append(f'<row r="{excel_row}" spans="1:28">')
            parts.append(f'<c r="A{excel_row}" t="n"><v>{serial}</v></c>')
            for column, value in enumerate(values, start=2):
                if value in (None, ""):
                    continue
                reference = (
                    f"{FastInterfaceOverviewGenerator._column_name(column)}{excel_row}"
                )
                text = str(value)[:32767]
                if text.startswith("="):
                    parts.append(
                        f'<c r="{reference}"><f>'
                        f'{FastInterfaceOverviewGenerator._xml_text(text[1:])}'
                        "</f><v></v></c>"
                    )
                else:
                    parts.append(
                        f'<c r="{reference}" t="inlineStr"><is><t '
                        f'xml:space="preserve">{FastInterfaceOverviewGenerator._xml_text(text)}</t>'
                        "</is></c>"
                    )
            parts.append("</row>")
        parts.append("</sheetData>")
        return "".join(parts).encode("utf-8")

    @staticmethod
    def _column_name(column: int) -> str:
        result = ""
        while column:
            column, remainder = divmod(column - 1, 26)
            result = chr(65 + remainder) + result
        return result

    @staticmethod
    def _xml_text(value: object) -> str:
        text = str(value)
        if re.search(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", text):
            raise ContractError("overview cell contains an illegal XML character")
        return escape(text)
