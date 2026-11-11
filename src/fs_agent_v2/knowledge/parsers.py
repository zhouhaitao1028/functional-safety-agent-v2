from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile
import json

from ..errors import KnowledgeError
from .models import TextSection


SUPPORTED_SUFFIXES = {".docx", ".xlsx", ".md", ".txt", ".json"}
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def parse_asset(path: Path) -> list[TextSection]:
    try:
        suffix = path.suffix.lower()
        if suffix == ".docx":
            return _parse_docx(path)
        if suffix == ".xlsx":
            return _parse_xlsx(path)
        if suffix in {".md", ".txt"}:
            return _parse_text(path)
        if suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            return [TextSection("json:$", json.dumps(value, ensure_ascii=False, indent=2))]
    except (OSError, BadZipFile, ET.ParseError, ValueError) as exc:
        raise KnowledgeError(f"failed to parse {path}: {exc}") from exc
    raise KnowledgeError(f"unsupported knowledge asset: {path.suffix}")


def chunk_sections(
    sections: list[TextSection], max_chars: int = 1400, overlap_chars: int = 120
) -> list[TextSection]:
    chunks: list[TextSection] = []
    pending_text: list[str] = []
    pending_locators: list[str] = []
    pending_length = 0

    def flush() -> None:
        nonlocal pending_text, pending_locators, pending_length
        if pending_text:
            chunks.append(
                TextSection(
                    _locator_range(pending_locators),
                    "\n".join(pending_text).strip(),
                )
            )
        pending_text = []
        pending_locators = []
        pending_length = 0

    for section in sections:
        text = section.text.strip()
        if not text:
            continue
        if len(text) > max_chars:
            flush()
            start = 0
            part = 1
            while start < len(text):
                end = min(len(text), start + max_chars)
                chunks.append(TextSection(f"{section.locator}:part-{part}", text[start:end]))
                if end == len(text):
                    break
                start = max(start + 1, end - overlap_chars)
                part += 1
            continue
        if pending_text and pending_length + len(text) + 1 > max_chars:
            flush()
        pending_text.append(text)
        pending_locators.append(section.locator)
        pending_length += len(text) + 1
    flush()
    return chunks


def _parse_text(path: Path) -> list[TextSection]:
    sections = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if line.strip():
            sections.append(TextSection(f"line:{line_number}", line.strip()))
    return sections


def _parse_docx(path: Path) -> list[TextSection]:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{WORD_NS}body")
    if body is None:
        return []
    sections: list[TextSection] = []
    paragraph_number = 0
    table_number = 0
    for child in body:
        if child.tag == f"{WORD_NS}p":
            paragraph_number += 1
            text = "".join(node.text or "" for node in child.iter(f"{WORD_NS}t")).strip()
            if text:
                sections.append(TextSection(f"paragraph:{paragraph_number}", text))
        elif child.tag == f"{WORD_NS}tbl":
            table_number += 1
            for row_number, row in enumerate(child.iter(f"{WORD_NS}tr"), 1):
                cells = []
                for cell in row.findall(f"{WORD_NS}tc"):
                    value = "".join(node.text or "" for node in cell.iter(f"{WORD_NS}t")).strip()
                    cells.append(value)
                if any(cells):
                    sections.append(
                        TextSection(f"table:{table_number}:row:{row_number}", " | ".join(cells))
                    )
    return sections


def _parse_xlsx(path: Path) -> list[TextSection]:
    with ZipFile(path) as archive:
        shared = _shared_strings(archive)
        sections: list[TextSection] = []
        sheet_paths = sorted(
            name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        for sheet_path in sheet_paths:
            sheet_name = Path(sheet_path).stem
            root = ET.fromstring(archive.read(sheet_path))
            for row_number, row in enumerate(root.iter(f"{SHEET_NS}row"), 1):
                values = []
                first_ref = ""
                for cell in row.findall(f"{SHEET_NS}c"):
                    reference = cell.attrib.get("r", "")
                    first_ref = first_ref or reference
                    values.append(_cell_value(cell, shared))
                if any(values):
                    locator = f"{sheet_name}!{first_ref or row_number}"
                    sections.append(TextSection(locator, " | ".join(values)))
        return sections


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{SHEET_NS}t"))
        for item in root.findall(f"{SHEET_NS}si")
    ]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{SHEET_NS}t")).strip()
    value = cell.find(f"{SHEET_NS}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError):
            return value.text
    return value.text


def _locator_range(locators: list[str]) -> str:
    if not locators:
        return "unknown"
    if len(locators) == 1:
        return locators[0]
    return f"{locators[0]}..{locators[-1]}"
