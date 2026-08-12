from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from lxml import etree
from PIL import Image

PACKAGE_NAMESPACE = uuid.UUID("6d4d259c-105b-5fee-a87a-efd4ad4d9bf8")
MANIFEST_SCHEMA = "https://hwaipy.github.io/Paper2HTML/schema/0.1/manifest.schema.json"
REPORT_SCHEMA = "https://hwaipy.github.io/Paper2HTML/schema/0.1/validation-report.schema.json"
XLINK = "http://www.w3.org/1999/xlink"
ARXIV_ISSN = "2331-8422"
ARXIV_ISSN_REGISTRY = "https://portal.issn.org/resource/ISSN-L/2331-8422"


class ConversionError(RuntimeError):
    """Raised when the converter cannot produce a complete package directory."""


@dataclass(frozen=True)
class ConversionOptions:
    created_at: str | None = None
    replace: bool = False
    allow_network: bool = False
    cache_dir: Path | None = None


@dataclass(frozen=True)
class TextBox:
    page: int
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float = 0.0
    confidence: float = 1.0


@dataclass
class ContentBlock:
    page: int
    text: str
    boxes: list[TextBox]
    kind: str = "paragraph"
    element_id: str = ""

    @property
    def regions(self) -> list[list[float]]:
        return [list(box.bbox) for box in self.boxes]


@dataclass
class PageData:
    number: int
    width_pt: float
    height_pt: float
    rotation: int
    image_path: Path
    image_width: int
    image_height: int
    native: list[TextBox] = field(default_factory=list)
    ocr: list[TextBox] = field(default_factory=list)


def _run(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConversionError(f"cannot run {command[0]}: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ConversionError(f"{command[0]} failed ({completed.returncode}): {detail}")
    return completed


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise ConversionError(f"required executable is unavailable: {name}")
    return executable


def _pdf_metadata(pdf: Path) -> tuple[int, list[tuple[float, float]], list[int]]:
    output = _run([_tool("pdfinfo"), "-box", str(pdf)]).stdout
    pages_match = re.search(r"^Pages:\s+(\d+)\s*$", output, re.MULTILINE)
    if not pages_match:
        raise ConversionError("pdfinfo did not report a page count")
    count = int(pages_match.group(1))
    sizes: list[tuple[float, float]] = []
    rotations: list[int] = []
    for page in range(1, count + 1):
        page_output = _run([_tool("pdfinfo"), "-f", str(page), "-l", str(page), "-box", str(pdf)]).stdout
        size_match = re.search(
            rf"^Page\s+{page}\s+size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts", page_output, re.MULTILINE
        ) or re.search(r"^Page size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts", page_output, re.MULTILINE)
        rotation_match = re.search(rf"^Page\s+{page}\s+rot:\s+(\d+)", page_output, re.MULTILINE) or re.search(
            r"^Page rot:\s+(\d+)", page_output, re.MULTILINE
        )
        if not size_match:
            raise ConversionError(f"pdfinfo did not report the size of page {page}")
        sizes.append((float(size_match.group(1)), float(size_match.group(2))))
        rotations.append(int(rotation_match.group(1)) % 360 if rotation_match else 0)
    return count, sizes, rotations


def _render_pages(pdf: Path, destination: Path, page_count: int) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p2h-render-") as temporary:
        prefix = Path(temporary) / "page"
        _run(
            [
                _tool("pdftoppm"),
                "-cropbox",
                "-r",
                "300",
                "-png",
                str(pdf),
                str(prefix),
            ],
            timeout=max(300, page_count * 60),
        )
        rendered = sorted(Path(temporary).glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        if len(rendered) != page_count:
            raise ConversionError(f"renderer produced {len(rendered)} pages; expected {page_count}")
        results: list[Path] = []
        for number, source in enumerate(rendered, 1):
            target = destination / f"page-{number:06d}.png"
            with Image.open(source) as image:
                rgb = image.convert("RGB")
                rgb.save(target, format="PNG", dpi=(300, 300), icc_profile=image.info.get("icc_profile"))
            results.append(target)
        return results


def _extract_native(pdf: Path, page_sizes: list[tuple[float, float]]) -> list[list[TextBox]]:
    with tempfile.TemporaryDirectory(prefix="p2h-native-") as temporary:
        xml_path = Path(temporary) / "layout.xml"
        _run(
            [
                _tool("pdftohtml"),
                "-xml",
                "-hidden",
                "-nodrm",
                "-zoom",
                "1.0",
                str(pdf),
                str(xml_path),
            ],
            timeout=max(300, len(page_sizes) * 30),
        )
        try:
            root = etree.parse(str(xml_path)).getroot()
        except (OSError, etree.XMLSyntaxError) as exc:
            raise ConversionError(f"cannot parse Poppler layout XML: {exc}") from exc
        pages: list[list[TextBox]] = [[] for _ in page_sizes]
        fonts = {node.get("id", ""): float(node.get("size", "0")) for node in root.findall(".//fontspec")}
        for page_node in root.findall("page"):
            number = int(page_node.get("number", "0"))
            if not 1 <= number <= len(page_sizes):
                continue
            xml_width = float(page_node.get("width", "0"))
            xml_height = float(page_node.get("height", "0"))
            if xml_width <= 0 or xml_height <= 0:
                continue
            for node in page_node.findall("text"):
                text = unicodedata.normalize("NFC", "".join(node.itertext()).strip())
                if not text:
                    continue
                left = float(node.get("left", "0"))
                top = float(node.get("top", "0"))
                width = float(node.get("width", "0"))
                height = float(node.get("height", "0"))
                if height <= 0:
                    continue
                if width <= 0:
                    # Poppler reports zero width for some rotated text runs
                    # (notably vertical repository stamps). Estimate their
                    # vertical extent from the actual glyph-run length while
                    # retaining the reported anchor and font height.
                    vertical_extent = min(xml_height, max(height, len(text) * height * 0.55))
                    bbox = _bounded_bbox(
                        left / xml_width,
                        max(0.0, top - vertical_extent) / xml_height,
                        (left + height) / xml_width,
                        min(xml_height, top + max(height, vertical_extent * 0.25)) / xml_height,
                    )
                else:
                    bbox = _bounded_bbox(
                        left / xml_width,
                        top / xml_height,
                        (left + width) / xml_width,
                        (top + height) / xml_height,
                    )
                pages[number - 1].append(
                    TextBox(number, text, bbox, fonts.get(node.get("font", ""), 0.0), 1.0)
                )
        return [_merge_line_fragments(page) for page in pages]


def _merge_line_fragments(boxes: list[TextBox]) -> list[TextBox]:
    """Join Poppler font runs on one visual line without joining columns."""
    output: list[TextBox] = []
    for box in sorted(boxes, key=lambda item: (item.bbox[1], item.bbox[0])):
        previous = output[-1] if output else None
        same_baseline = previous is not None and abs(previous.bbox[1] - box.bbox[1]) < 0.004
        horizontal_gap = box.bbox[0] - previous.bbox[2] if previous else 1.0
        if same_baseline and -0.01 <= horizontal_gap < 0.06:
            joiner = "" if horizontal_gap < 0.004 or previous.text.endswith(("(", "[", "{")) else " "
            bbox = _bounded_bbox(
                min(previous.bbox[0], box.bbox[0]),
                min(previous.bbox[1], box.bbox[1]),
                max(previous.bbox[2], box.bbox[2]),
                max(previous.bbox[3], box.bbox[3]),
            )
            output[-1] = TextBox(
                box.page,
                previous.text + joiner + box.text,
                bbox,
                max(previous.font_size, box.font_size),
                1.0,
            )
        else:
            output.append(box)
    return output


def _extract_vision(images: list[Path]) -> list[list[TextBox]]:
    if os.uname().sysname != "Darwin":
        raise ConversionError("the minimal OCR backend currently requires macOS Vision")
    script = Path(__file__).with_name("vision_ocr.swift")
    completed = _run(
        [_tool("xcrun"), "swift", str(script), *(str(path) for path in images)],
        timeout=max(600, len(images) * 120),
    )
    pages: list[list[TextBox]] = [[] for _ in images]
    try:
        for line in completed.stdout.splitlines():
            value = json.loads(line)
            page = int(value["page"])
            pages[page - 1] = [
                TextBox(
                    page,
                    unicodedata.normalize("NFC", item["text"]),
                    _bounded_bbox(*item["bbox"]),
                    confidence=float(item["confidence"]),
                )
                for item in value["observations"]
                if item.get("text")
            ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConversionError(f"cannot parse Vision OCR output: {exc}") from exc
    if any(not page for page in pages):
        missing = [str(i) for i, page in enumerate(pages, 1) if not page]
        raise ConversionError(f"Vision OCR returned no text for page(s): {', '.join(missing)}")
    return pages


def _bounded_bbox(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    values = [round(max(0.0, min(1.0, float(v))), 6) for v in (x0, y0, x1, y1)]
    if values[2] <= values[0]:
        values[2] = min(1.0, values[0] + 0.000001)
    if values[3] <= values[1]:
        values[3] = min(1.0, values[1] + 0.000001)
    return tuple(values)  # type: ignore[return-value]


def _reading_order(boxes: list[TextBox]) -> list[TextBox]:
    if not boxes:
        return []
    full = [box for box in boxes if box.bbox[2] - box.bbox[0] >= 0.58 or box.bbox[0] < 0.46 < box.bbox[2]]
    narrow = [box for box in boxes if box not in full]
    left = [box for box in narrow if (box.bbox[0] + box.bbox[2]) / 2 < 0.5]
    right = [box for box in narrow if box not in left]

    def key(box: TextBox) -> tuple[float, float]:
        return round(box.bbox[1], 4), box.bbox[0]

    if len(left) < 2 or len(right) < 2:
        return sorted(boxes, key=key)
    column_top = min(box.bbox[1] for box in narrow)
    column_bottom = max(box.bbox[3] for box in narrow)
    leading = [box for box in full if box.bbox[3] <= column_top]
    trailing = [box for box in full if box.bbox[1] >= column_bottom]
    middle = [box for box in full if box not in leading and box not in trailing]
    return (
        sorted(leading, key=key)
        + sorted(left, key=key)
        + sorted(right, key=key)
        + sorted(middle, key=key)
        + sorted(trailing, key=key)
    )


def _is_marginal_vertical_stamp(box: TextBox) -> bool:
    width = box.bbox[2] - box.bbox[0]
    height = box.bbox[3] - box.bbox[1]
    at_side = box.bbox[0] < 0.12 or box.bbox[2] > 0.88
    return at_side and len(box.text) >= 12 and (height > width * 2.5 or width < 0.08)


def _title_candidate_score(box: TextBox) -> tuple[float, float, float, float] | None:
    width = box.bbox[2] - box.bbox[0]
    height = box.bbox[3] - box.bbox[1]
    if _is_marginal_vertical_stamp(box) or width < 0.18 or width < height * 2.5:
        return None
    if sum(character.isalnum() for character in box.text) < 3:
        return None
    center = (box.bbox[0] + box.bbox[2]) / 2
    return box.font_size, width, -abs(center - 0.5), -box.bbox[1]


def _group_blocks(pages: list[PageData]) -> tuple[ContentBlock, list[ContentBlock]]:
    all_boxes = [box for page in pages for box in page.native]
    if not all_boxes:
        raise ConversionError(
            "the PDF has no extractable native text; scanned-PDF structure is not implemented yet"
        )
    first_candidates = [
        (score, box)
        for box in pages[0].native
        if box.bbox[1] < 0.45 and (score := _title_candidate_score(box)) is not None
    ]
    if first_candidates:
        title_box = max(first_candidates, key=lambda item: item[0])[1]
    else:
        horizontal = [box for box in pages[0].native if not _is_marginal_vertical_stamp(box)]
        title_box = max(horizontal or pages[0].native, key=lambda box: (box.font_size, len(box.text)))
    title = ContentBlock(1, title_box.text, [title_box], "title", "title-000002")
    blocks: list[ContentBlock] = []
    for page in pages:
        ordered = [
            box
            for box in _reading_order(page.native)
            if box is not title_box and not _is_marginal_vertical_stamp(box)
        ]
        font_sizes = [box.font_size for box in ordered if box.font_size > 0]
        median_font = statistics.median(font_sizes) if font_sizes else 0.0
        for box in ordered:
            is_heading = bool(
                re.match(r"^(?:[A-Z]\d+|[IVXLCDM]+|\d+(?:\.\d+)*)\.?\s+[A-Z]", box.text)
                and len(box.text) <= 120
                and box.font_size >= median_font * 1.1
            )
            if is_heading:
                blocks.append(ContentBlock(page.number, box.text, [box], "heading"))
                continue
            previous = blocks[-1] if blocks else None
            same_page = previous is not None and previous.page == page.number
            starts_reference = re.match(r"^\[\d+\]", box.text) is not None
            outdented = previous is not None and box.bbox[0] < previous.boxes[-1].bbox[0] - 0.02
            same_column = previous and abs(previous.boxes[-1].bbox[0] - box.bbox[0]) < 0.08
            vertical_gap = box.bbox[1] - previous.boxes[-1].bbox[3] if previous else 1.0
            compatible = (
                previous
                and same_page
                and previous.kind == "paragraph"
                and same_column
                and vertical_gap < 0.02
                and not starts_reference
                and not outdented
            )
            if compatible and not previous.text.endswith((".", "?", "!", ":")):
                next_word = box.text.split(maxsplit=1)[0].lower()
                if previous.text.endswith("-") and next_word in {"and", "or"}:
                    previous.text += " " + box.text
                elif previous.text.endswith("-") and next_word.startswith(("and-", "or-")):
                    previous.text += box.text
                elif previous.text.endswith("-"):
                    previous.text = previous.text.removesuffix("-") + box.text
                else:
                    previous.text += " " + box.text
                previous.boxes.append(box)
            else:
                blocks.append(ContentBlock(page.number, box.text, [box]))
    return title, blocks


def _assign_ids(blocks: list[ContentBlock]) -> None:
    paragraph = 0
    section = 0
    title = 2
    for block in blocks:
        if block.kind == "heading":
            section += 1
            block.element_id = f"sec-{section:06d}"
            title += 1
            block.kind = f"heading:title-{title:06d}"
        else:
            paragraph += 1
            block.element_id = f"p-{paragraph:06d}"


def _build_xml(
    title: ContentBlock,
    blocks: list[ContentBlock],
    publication_id: str,
    publication_label: str,
    publication_box: TextBox,
    publication_id_type: str,
    publication_sourced: bool,
) -> tuple[bytes, list[ContentBlock]]:
    nsmap = {"xlink": XLINK}
    article = etree.Element(
        "article",
        nsmap=nsmap,
        id="doc-000001",
        attrib={
            "dtd-version": "1.3",
            "{http://www.w3.org/XML/1998/namespace}lang": "en",
            "article-type": "research-article",
        },
    )
    front = etree.SubElement(article, "front")
    journal = etree.SubElement(front, "journal-meta")
    journal_id = etree.SubElement(
        journal, "journal-id", id="journal-id-000001", attrib={"journal-id-type": "publisher-id"}
    )
    journal_id.text = publication_label
    group = etree.SubElement(journal, "journal-title-group")
    journal_title = etree.SubElement(group, "journal-title", id="title-000001")
    journal_title.text = publication_label
    if publication_label == "arXiv":
        issn = etree.SubElement(
            journal,
            "issn",
            id="issn-000001",
            attrib={"publication-format": "electronic", "specific-use": "registry-derived"},
        )
        issn.text = ARXIV_ISSN
    else:
        etree.SubElement(
            journal,
            "issn",
            attrib={"publication-format": "electronic", "specific-use": "not-applicable"},
        )
    meta = etree.SubElement(front, "article-meta")
    article_id = etree.SubElement(
        meta,
        "article-id",
        id="article-id-000001",
        attrib={"pub-id-type": publication_id_type},
    )
    article_id.text = publication_id
    title_group = etree.SubElement(meta, "title-group")
    article_title = etree.SubElement(title_group, "article-title", id=title.element_id)
    article_title.text = title.text
    body = etree.SubElement(article, "body")
    current = body
    for block in blocks:
        if block.kind.startswith("heading:"):
            current = etree.SubElement(body, "sec", id=block.element_id)
            heading = etree.SubElement(current, "title", id=block.kind.split(":", 1)[1])
            heading.text = block.text
        else:
            paragraph = etree.SubElement(current, "p", id=block.element_id)
            paragraph.text = block.text
    etree.SubElement(article, "back")
    xml = etree.tostring(article, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    meta_blocks = [title]
    if publication_sourced:
        meta_blocks[:0] = [
            ContentBlock(
                publication_box.page,
                publication_label,
                [publication_box],
                "metadata",
                "journal-id-000001",
            ),
            ContentBlock(
                publication_box.page,
                publication_label,
                [publication_box],
                "metadata",
                "title-000001",
            ),
            ContentBlock(
                publication_box.page,
                ARXIV_ISSN,
                [publication_box],
                "derived-issn",
                "issn-000001",
            ),
            ContentBlock(
                publication_box.page,
                publication_id,
                [publication_box],
                "metadata",
                "article-id-000001",
            ),
        ]
    expanded: list[ContentBlock] = []
    for block in blocks:
        if block.kind.startswith("heading:"):
            expanded.append(ContentBlock(block.page, block.text, block.boxes, "section", block.element_id))
            expanded.append(
                ContentBlock(block.page, block.text, block.boxes, "title", block.kind.split(":", 1)[1])
            )
        else:
            expanded.append(block)
    return xml, meta_blocks + expanded


def _intersection(a: Iterable[float], b: Iterable[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    width = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    height = max(0.0, min(ay1, by1) - max(ay0, by0))
    return width * height


def _ocr_candidate(block: ContentBlock, page: PageData) -> tuple[str, float]:
    matches = [
        box for box in page.ocr if any(_intersection(box.bbox, native.bbox) > 0 for native in block.boxes)
    ]
    matches = _reading_order(matches)
    if not matches:
        return "", 0.0
    return " ".join(box.text for box in matches), sum(box.confidence for box in matches) / len(matches)


def _records(
    blocks: list[ContentBlock], pages: list[PageData], revision_timestamp: str
) -> list[dict[str, Any]]:
    page_by_number = {page.number: page for page in pages}
    records: list[dict[str, Any]] = []
    for order, block in enumerate(blocks, 1):
        grouped: dict[int, list[TextBox]] = {}
        for box in block.boxes:
            grouped.setdefault(box.page, []).append(box)
        sources: list[dict[str, Any]] = []
        for page_number, boxes in sorted(grouped.items()):
            page = page_by_number[page_number]
            candidate_block = ContentBlock(page_number, block.text, boxes)
            ocr_text, confidence = _ocr_candidate(candidate_block, page)
            native_text = " ".join(box.text for box in boxes)
            sources.append(
                {
                    "source_id": "src-001",
                    "physical_page": page_number,
                    "logical_page_id": f"lp-{page_number:06d}",
                    "page_image": f"assets/evidence/pages/src-001/page-{page_number:06d}.png",
                    "regions": [
                        {"bbox": list(box.bbox)}
                        for box in sorted(boxes, key=lambda b: (b.bbox[1], b.bbox[0]))
                    ],
                    "candidates": [
                        {
                            "method": "native-pdf",
                            "engine": "poppler-pdftohtml",
                            "engine_version": _poppler_version(),
                            "text": native_text,
                            "confidence": 1.0,
                        },
                        {
                            "method": "ocr",
                            "engine": "apple-vision",
                            "engine_version": _vision_version(),
                            "text": ocr_text,
                            "confidence": round(confidence, 6),
                        },
                    ],
                }
            )
        revisions: list[dict[str, Any]] = []
        if block.kind == "derived-issn":
            evidence_box = block.boxes[0]
            revisions.append(
                {
                    "timestamp": revision_timestamp,
                    "actor": "software:paper2html-minimal-converter",
                    "method": "automatic",
                    "before": "",
                    "after": block.text,
                    "reason": "Derived the registered arXiv.org ISSN from the source-backed arXiv identity.",
                    "evidence": [
                        {
                            "source_id": "src-001",
                            "physical_page": evidence_box.page,
                            "page_image": (f"assets/evidence/pages/src-001/page-{evidence_box.page:06d}.png"),
                            "bbox": list(evidence_box.bbox),
                        }
                    ],
                    "x-registry": ARXIV_ISSN_REGISTRY,
                }
            )
        records.append(
            {
                "element_id": block.element_id,
                "xml_path": f"//*[@id='{block.element_id}']",
                "reading_order": order,
                "sources": sources,
                "decision": {"method": "reconciled", "confidence": 0.8},
                "revisions": revisions,
            }
        )
    return records


@lru_cache(maxsize=1)
def _poppler_version() -> str:
    output = _run([_tool("pdftohtml"), "-v"]).stderr
    match = re.search(r"version\s+([^\s]+)", output)
    return match.group(1) if match else "unknown"


@lru_cache(maxsize=1)
def _vision_version() -> str:
    output = _run(["sw_vers", "-productVersion"]).stdout.strip()
    return output or "unknown"


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _jsonl_dump(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values)
    path.write_text(text, encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksums(root: Path) -> None:
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.name != "checksums.sha256"),
        key=lambda path: path.relative_to(root).as_posix().encode(),
    )
    payload = "".join(f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n" for path in files)
    (root / "checksums.sha256").write_text(payload, encoding="utf-8", newline="\n")


def _placeholder_report() -> dict[str, Any]:
    return {
        "$schema": REPORT_SCHEMA,
        "format": "paper2html-validation-report",
        "format_version": "0.1",
        "valid": False,
        "validated_at": "1970-01-01T00:00:00Z",
        "checks": {
            name: "not-run"
            for name in (
                "manifest_schema",
                "xml_well_formed",
                "jats_bits_schema",
                "p2h_profile",
                "id_uniqueness",
                "cross_references",
                "page_coverage",
                "element_provenance",
                "asset_integrity",
                "checksum_integrity",
            )
        },
        "errors": [{"code": "validation_not_run", "message": "Package validation has not run."}],
        "warnings": [],
    }


def _atomic_destination(output: Path, replace: bool) -> tuple[Path, Callable[[], None]]:
    output = output.resolve()
    if output.exists() and not replace:
        raise ConversionError(f"output already exists (use --replace): {output}")
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise ConversionError(f"refusing to replace non-directory output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))

    def publish() -> None:
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{output.name}-previous-", dir=output.parent))
            backup.rmdir()
            os.replace(output, backup)
            try:
                os.replace(temporary, output)
            except Exception:
                os.replace(backup, output)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(temporary, output)

    return temporary, publish


def _validate_output_path(pdf: Path, output: Path) -> Path:
    """Resolve a safe destination without creating or modifying any path."""
    raw = output.absolute()
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ConversionError(f"output path must not contain symbolic links: {current}")
        if not current.exists():
            break
    resolved = raw.resolve(strict=False)
    if resolved == pdf or resolved in pdf.parents:
        raise ConversionError("output must not equal or contain the input PDF path")
    return resolved


def convert_pdf(pdf: Path, output: Path, options: ConversionOptions | None = None) -> dict[str, Any]:
    options = options or ConversionOptions()
    pdf = pdf.resolve()
    if not pdf.is_file():
        raise ConversionError(f"input PDF does not exist: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise ConversionError("the minimal converter accepts PDF input only")
    safe_output = _validate_output_path(pdf, output)
    root, publish = _atomic_destination(safe_output, options.replace)
    try:
        page_count, sizes, rotations = _pdf_metadata(pdf)
        image_dir = root / "assets/evidence/pages/src-001"
        images = _render_pages(pdf, image_dir, page_count)
        native_pages = _extract_native(pdf, sizes)
        ocr_pages = _extract_vision(images)
        pages: list[PageData] = []
        for number, (size, rotation, image_path, native, ocr) in enumerate(
            zip(sizes, rotations, images, native_pages, ocr_pages, strict=True), 1
        ):
            with Image.open(image_path) as image:
                pixels = image.size
            width_pt, height_pt = size
            if rotation in {90, 270}:
                width_pt, height_pt = height_pt, width_pt
            pages.append(PageData(number, width_pt, height_pt, rotation, image_path, *pixels, native, ocr))
        title, body_blocks = _group_blocks(pages)
        _assign_ids(body_blocks)
        joined_native = " ".join(box.text for page in pages for box in page.native)
        arxiv_match = re.search(r"(?:arXiv:)?(\d{4}\.\d{4,5}v\d+)", joined_native, re.IGNORECASE)
        if arxiv_match:
            publication_id = arxiv_match.group(1)
            publication_label = "arXiv"
            publication_id_type = "arxiv"
            publication_sourced = True
            publication_box = next(
                box for page in pages for box in page.native if publication_id.lower() in box.text.lower()
            )
        else:
            publication_id = pdf.stem
            publication_label = "Unknown publication"
            publication_id_type = "publisher-id"
            publication_box = title.boxes[0]
            publication_sourced = False
        xml, addressable = _build_xml(
            title,
            body_blocks,
            publication_id,
            publication_label,
            publication_box,
            publication_id_type,
            publication_sourced,
        )
        (root / "content").mkdir(parents=True)
        (root / "content/document.xml").write_bytes(xml)
        page_records = []
        for page in pages:
            page_records.append(
                {
                    "source_id": "src-001",
                    "physical_page": page.number,
                    "logical_page_id": f"lp-{page.number:06d}",
                    "printed_label": None,
                    "width_pt": page.width_pt,
                    "height_pt": page.height_pt,
                    "rotation_degrees": page.rotation,
                    "image": f"assets/evidence/pages/src-001/page-{page.number:06d}.png",
                    "image_width_px": page.image_width,
                    "image_height_px": page.image_height,
                    "render_dpi": 300,
                    "ocr_status": "completed" if page.ocr else "no-text",
                }
            )
        created_at = options.created_at or datetime.now(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        _jsonl_dump(root / "provenance/pages.jsonl", page_records)
        _jsonl_dump(root / "provenance/elements.jsonl", _records(addressable, pages, created_at))
        _jsonl_dump(root / "provenance/omissions.jsonl", [])
        source_sha = _sha256(pdf)
        manifest = {
            "$schema": MANIFEST_SCHEMA,
            "format": "paper2html-package",
            "format_version": "0.1",
            "package_id": f"urn:uuid:{uuid.uuid5(PACKAGE_NAMESPACE, f'paper2html-package:0.1:{source_sha}')}",
            "created_at": created_at,
            "generator": {"name": "paper2html-minimal-converter", "version": "0.1.0"},
            "document": {
                "id": "doc-000001",
                "type": "article",
                "profile": "jats-1.3",
                "language": "en",
                "content": "content/document.xml",
            },
            "sources": [
                {
                    "id": "src-001",
                    "role": "primary",
                    "original_name": pdf.name,
                    "media_type": "application/pdf",
                    "sha256": source_sha,
                    "size": pdf.stat().st_size,
                    "page_count": page_count,
                    "source_class": "born-digital",
                    "extraction_modes": ["native-pdf", "ocr"],
                    "embedded_path": None,
                }
            ],
            "provenance": {
                "pages": "provenance/pages.jsonl",
                "elements": "provenance/elements.jsonl",
                "omissions": "provenance/omissions.jsonl",
            },
            "validation": "validation/report.json",
            "checksums": "checksums.sha256",
        }
        _json_dump(root / "manifest.json", manifest)
        _json_dump(root / "validation/report.json", _placeholder_report())
        _write_checksums(root)
        validator = importlib.import_module("src.validator.validator")
        validation_options = validator.ValidationOptions(
            cache_dir=options.cache_dir, allow_network=options.allow_network
        )
        result = validator.validate_package(
            root,
            validation_options,
            writing_report=True,
        )
        if result.operational_error:
            details = "; ".join(item["message"] for item in result.report["errors"][:3])
            raise ConversionError(f"validator could not execute: {details}")
        result.report["validated_at"] = created_at
        validator.write_report(root, result)
        publish()
        return result.report
    except Exception:
        if root.exists():
            shutil.rmtree(root)
        raise
