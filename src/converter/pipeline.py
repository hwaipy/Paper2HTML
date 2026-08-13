from __future__ import annotations

import fcntl
import hashlib
import http.client
import importlib
import ipaddress
import json
import os
import queue
import re
import shutil
import socket
import ssl
import statistics
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import certifi
from lxml import etree
from PIL import Image
from wordfreq import get_frequency_dict, zipf_frequency

PACKAGE_NAMESPACE = uuid.UUID("6d4d259c-105b-5fee-a87a-efd4ad4d9bf8")
MANIFEST_SCHEMA = "https://hwaipy.github.io/Paper2HTML/schema/0.1/manifest.schema.json"
REPORT_SCHEMA = "https://hwaipy.github.io/Paper2HTML/schema/0.1/validation-report.schema.json"
XLINK = "http://www.w3.org/1999/xlink"
ARXIV_ISSN = "2331-8422"
ARXIV_ISSN_REGISTRY = "https://portal.issn.org/resource/ISSN-L/2331-8422"
SOURCE_DESCRIPTOR_FORMAT = "paper2html-pdf-source"
SOURCE_DESCRIPTOR_VERSION = "1"
MAX_REMOTE_PDF_BYTES = 100 * 1024 * 1024
MAX_REMOTE_REDIRECTS = 5
REMOTE_TIMEOUT_SECONDS = 30
REMOTE_TOTAL_TIMEOUT_SECONDS = 120
DOH_HOST = "cloudflare-dns.com"
DOH_ADDRESS = "1.1.1.1"


class ConversionError(RuntimeError):
    """Raised when the converter cannot produce a complete package directory."""


@dataclass(frozen=True)
class ConversionOptions:
    created_at: str | None = None
    replace: bool = False
    allow_network: bool = False
    cache_dir: Path | None = None
    download_cache_dir: Path | None = None
    secure_dns: bool = False


@dataclass(frozen=True)
class ResolvedPDF:
    path: Path
    original_name: str
    source_url: str | None = None
    final_url: str | None = None


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
    child_ids: list[str] = field(default_factory=list)
    caption_boxes: list[TextBox] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    revision_before: str | None = None
    aff_rids: list[str] = field(default_factory=list)
    equal_contribution: bool = False
    derived_bbox: tuple[float, float, float, float] | None = None

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


@dataclass
class FrontMatter:
    authors: list[ContentBlock] = field(default_factory=list)
    affiliations: list[ContentBlock] = field(default_factory=list)
    abstract: ContentBlock | None = None
    publication_date: ContentBlock | None = None
    contribution_note: ContentBlock | None = None


@dataclass
class StructureResult:
    title: ContentBlock
    front: FrontMatter
    blocks: list[ContentBlock]
    omissions: list[dict[str, Any]]


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
            joiner = " " if horizontal_gap >= 0.004 or _needs_interrun_space(previous.text, box.text) else ""
            bbox = _bounded_bbox(
                min(previous.bbox[0], box.bbox[0]),
                min(previous.bbox[1], box.bbox[1]),
                max(previous.bbox[2], box.bbox[2]),
                max(previous.bbox[3], box.bbox[3]),
            )
            output[-1] = TextBox(
                box.page,
                _repair_token_spacing(previous.text + joiner + box.text),
                bbox,
                max(previous.font_size, box.font_size),
                1.0,
            )
        else:
            output.append(
                TextBox(
                    box.page,
                    _repair_token_spacing(box.text),
                    box.bbox,
                    box.font_size,
                    box.confidence,
                )
            )
    return output


def _needs_interrun_space(left: str, right: str) -> bool:
    if not left or not right or left.endswith(("(", "[", "{")):
        return False
    a, b = left[-1], right[0]
    a_latin = "A" <= a <= "Z" or "a" <= a <= "z"
    b_latin = "A" <= b <= "Z" or "a" <= b <= "z"
    if a.islower() and b.isupper():
        return True
    if a.isalpha() and b.isalpha() and a_latin != b_latin:
        return True
    return a.isdigit() and b.isalpha() and len(right) > 1


def _repair_token_spacing(text: str) -> str:
    text = re.sub(r"(?<=[a-z])(?=[A-Z][A-Z])", " ", text)
    text = re.sub(r"(?<=[∆δφϕηνµε])(?=[A-Za-z])", " ", text)
    text = re.sub(r"\b(ns|ms|km|Hz)(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(
        r"\b([A-Za-z]+)-([a-z]+)\b",
        lambda match: (
            match.group(1) + match.group(2)
            if _inrun_hyphen_is_soft(match.group(1), match.group(2))
            else match.group(0)
        ),
        text,
    )
    return unicodedata.normalize("NFC", text)


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

    wide_prose = [box for box in boxes if box.bbox[2] - box.bbox[0] >= 0.45]
    if len(left) < 2 or len(right) < 2 or len(wide_prose) >= len(boxes) * 0.35:
        return sorted(boxes, key=key)
    output: list[TextBox] = []
    band: list[TextBox] = []

    def flush() -> None:
        band_left = [box for box in band if (box.bbox[0] + box.bbox[2]) / 2 < 0.5]
        band_right = [box for box in band if box not in band_left]
        output.extend(sorted(band_left, key=key))
        output.extend(sorted(band_right, key=key))
        band.clear()

    for box in sorted(boxes, key=key):
        if box in full:
            flush()
            output.append(box)
        else:
            band.append(box)
    flush()
    return output


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


def _join_lines(boxes: list[TextBox]) -> str:
    text = ""
    for box in boxes:
        next_word = box.text.split(maxsplit=1)[0].casefold() if box.text else ""
        if text.endswith("-") and next_word in {"and", "or"}:
            text += " " + box.text
        elif text.endswith("-") and next_word.startswith(("and-", "or-")):
            text += box.text
        elif text.endswith("-") and box.text[:1].islower():
            if _line_break_hyphen_is_soft(text, box.text):
                text = text[:-1] + box.text
            else:
                text += box.text
        else:
            text += (" " if text else "") + box.text
    return _repair_token_spacing(" ".join(text.split()))


@lru_cache(maxsize=1)
def _english_word_frequencies() -> dict[str, float]:
    return get_frequency_dict("en", wordlist="best")


def _line_break_hyphen_is_soft(left: str, right: str) -> bool:
    """Use a version-locked lexicon to distinguish soft hyphens from compounds."""
    left_match = re.search(r"([A-Za-z]+)-$", left)
    right_match = re.match(r"([A-Za-z]+)", right)
    if not left_match or not right_match:
        return False
    joined = (left_match.group(1) + right_match.group(1)).casefold()
    hyphenated = (left_match.group(1) + "-" + right_match.group(1)).casefold()
    lexicon = _english_word_frequencies()
    return joined in lexicon and zipf_frequency(joined, "en") >= 2.5 and hyphenated not in lexicon


def _inrun_hyphen_is_soft(left: str, right: str) -> bool:
    """Repair a soft line-break hyphen already merged into one Poppler run."""
    joined = (left + right).casefold()
    hyphenated = (left + "-" + right).casefold()
    lexicon = _english_word_frequencies()
    joined_has_stronger_evidence = (
        joined in lexicon and zipf_frequency(joined, "en") >= zipf_frequency(hyphenated, "en") + 0.5
    )
    productive_suffix = right.casefold() in {
        "able",
        "ance",
        "ation",
        "ed",
        "ence",
        "ible",
        "ing",
        "ion",
        "ity",
        "ive",
        "less",
        "ly",
        "ment",
        "ness",
        "ous",
        "sion",
        "tion",
    }
    return joined_has_stronger_evidence or productive_suffix and left.casefold() in lexicon


def _front_matter(pages: list[PageData], title_box: TextBox) -> tuple[FrontMatter, set[TextBox]]:
    page = pages[0]
    ordered = sorted(page.native, key=lambda box: (box.bbox[1], box.bbox[0]))
    consumed = {title_box}
    abstract_label = next((box for box in ordered if box.text.strip().casefold() == "abstract"), None)
    abstract_top = abstract_label.bbox[1] if abstract_label else 0.7
    possible_author_lines = [
        box
        for box in ordered
        if title_box.bbox[3] < box.bbox[1] < abstract_top
        and not _is_marginal_vertical_stamp(box)
        and box.font_size >= max(10.5, title_box.font_size * 0.6)
        and "," in box.text
    ]
    author_font = max((box.font_size for box in possible_author_lines), default=0.0)
    author_lines = [box for box in possible_author_lines if box.font_size >= author_font - 0.5]
    author_bottom = max((box.bbox[3] for box in author_lines), default=title_box.bbox[3])
    author_markers = [
        box
        for box in ordered
        if title_box.bbox[3] < box.bbox[1] <= author_bottom + 0.012
        and box not in author_lines
        and re.fullmatch(r"[0-9,†]+", box.text.strip()) is not None
    ]
    authors: list[ContentBlock] = []
    for line in author_lines:
        name_matches = [
            match for match in re.finditer(r"[^,]+", line.text) if re.search(r"[A-Za-z]", match.group())
        ]
        line_markers = sorted(
            [marker for marker in author_markers if abs(marker.bbox[1] - line.bbox[1]) < 0.015],
            key=lambda item: item.bbox[0],
        )
        for index, match in enumerate(name_matches):
            name = match.group().strip()
            if not name or not re.search(r"[A-Za-z]", name):
                continue
            leading = len(match.group()) - len(match.group().lstrip())
            start = match.start() + leading
            end = start + len(name)
            width = line.bbox[2] - line.bbox[0]
            sliced = TextBox(
                line.page,
                name,
                _bounded_bbox(
                    line.bbox[0] + width * start / len(line.text),
                    line.bbox[1],
                    line.bbox[0] + width * end / len(line.text),
                    line.bbox[3],
                ),
                line.font_size,
            )
            candidates = [
                marker
                for marker in author_markers
                if sliced.bbox[0] - 0.015 <= (marker.bbox[0] + marker.bbox[2]) / 2 <= sliced.bbox[2] + 0.035
                and abs(marker.bbox[1] - line.bbox[1]) < 0.015
            ]
            marker = (
                line_markers[index]
                if len(line_markers) == len(name_matches)
                else min(candidates, key=lambda item: abs(item.bbox[0] - sliced.bbox[2]))
                if candidates
                else None
            )
            marker_text = marker.text if marker else ""
            raw_boxes = [line, *([marker] if marker else [])]
            authors.append(
                ContentBlock(
                    1,
                    name,
                    raw_boxes,
                    "author",
                    revision_before=_join_lines(raw_boxes),
                    aff_rids=[f"aff-{int(value):06d}" for value in re.findall(r"\d+", marker_text)],
                    equal_contribution="†" in marker_text,
                    derived_bbox=sliced.bbox,
                )
            )
    consumed.update(author_lines)
    consumed.update(author_markers)
    affiliation_lines = [
        box
        for box in ordered
        if author_lines
        and author_bottom < box.bbox[1] < abstract_top
        and not _is_marginal_vertical_stamp(box)
        and 8.5 <= box.font_size <= 11.5
        and not box.text.startswith("†")
    ]
    affiliations: list[ContentBlock] = []
    current: list[TextBox] = []
    for box in affiliation_lines:
        if re.match(r"^[1-9]\d?\s+(?=[A-Z])", box.text) and current:
            affiliations.append(ContentBlock(1, _join_lines(current), current, "affiliation"))
            current = []
        current.append(box)
    if current:
        affiliations.append(ContentBlock(1, _join_lines(current), current, "affiliation"))
    consumed.update(affiliation_lines)

    abstract: ContentBlock | None = None
    if abstract_label is not None:
        consumed.add(abstract_label)
        abstract_boxes = [
            box for box in ordered if box.bbox[1] > abstract_label.bbox[3] and not _is_page_number(box)
        ]
        if abstract_boxes and len(pages) > 1 and abstract_boxes[-1].bbox[3] > 0.68:
            continuation = sorted(pages[1].native, key=lambda box: (box.bbox[1], box.bbox[0]))
            previous: TextBox | None = None
            for box in continuation:
                if _is_marginal_vertical_stamp(box) or _is_page_number(box):
                    continue
                if previous is not None and box.bbox[1] - previous.bbox[3] > 0.025:
                    break
                if abstract_boxes and abs(box.font_size - abstract_boxes[-1].font_size) > 1.0:
                    break
                abstract_boxes.append(box)
                previous = box
        consumed.update(abstract_boxes)
        abstract = ContentBlock(1, _join_lines(abstract_boxes), abstract_boxes, "abstract")

    publication_date: ContentBlock | None = None
    for box in ordered:
        if not _is_marginal_vertical_stamp(box):
            continue
        date_match = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\b", box.text)
        if date_match:
            months = {
                name: index
                for index, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)
            }
            month = months.get(date_match.group(2).title())
            if month:
                publication_date = ContentBlock(
                    box.page,
                    f"{int(date_match.group(1))}{month}{date_match.group(3)}",
                    [box],
                    f"pub-date:{int(date_match.group(1))}:{month}:{date_match.group(3)}",
                    revision_before=box.text,
                )
                consumed.add(box)
                break
    authorship_notes = [
        box for box in ordered if author_bottom < box.bbox[1] < abstract_top and box.text.startswith("†")
    ]
    consumed.update(authorship_notes)
    contribution_note = (
        ContentBlock(1, _join_lines(authorship_notes), authorship_notes, "contribution-note")
        if authorship_notes
        else None
    )
    return FrontMatter(authors, affiliations, abstract, publication_date, contribution_note), consumed


def _is_page_number(box: TextBox) -> bool:
    return box.bbox[1] > 0.74 and re.fullmatch(r"\d{1,4}", box.text.strip()) is not None


def _omission(box: TextBox, kind: str, reason: str) -> dict[str, Any]:
    return {
        "source_id": "src-001",
        "physical_page": box.page,
        "logical_page_id": f"lp-{box.page:06d}",
        "page_image": f"assets/evidence/pages/src-001/page-{box.page:06d}.png",
        "bbox": list(box.bbox),
        "type": kind,
        "reason": reason,
    }


def _figure_blocks(pages: list[PageData]) -> tuple[list[ContentBlock], set[TextBox]]:
    figures: list[ContentBlock] = []
    consumed: set[TextBox] = set()
    for page in pages:
        boxes = sorted(page.native, key=lambda box: (box.bbox[1], box.bbox[0]))
        starts = [box for box in boxes if re.match(r"^Fig\.\s*\d+\b", box.text)]
        for start in starts:
            if start.bbox[1] >= 0.6:
                continue
            caption_boxes = [
                box
                for box in boxes
                if start.bbox[1] - 0.002 <= box.bbox[1]
                and box.bbox[3] <= start.bbox[1] + 0.23
                and box.font_size <= start.font_size + 0.5
                and box.bbox[0] >= start.bbox[0] - 0.02
            ]
            caption_boxes.sort(key=lambda box: (box.bbox[1], box.bbox[0]))
            if not caption_boxes:
                continue
            last_bottom = caption_boxes[0].bbox[3]
            contiguous: list[TextBox] = []
            for box in caption_boxes:
                if contiguous and box.bbox[1] - last_bottom > 0.018:
                    break
                contiguous.append(box)
                last_bottom = max(last_bottom, box.bbox[3])
            caption_boxes = contiguous
            prior = [box for box in boxes if box.bbox[3] < start.bbox[1] - 0.005 and not _is_page_number(box)]
            if len(prior) < 3:
                continue
            y0 = max(0.0, min(box.bbox[1] for box in prior) - 0.01)
            graphic_bbox = _bounded_bbox(0.14, y0, 0.86, start.bbox[1] - 0.008)
            graphic_box = TextBox(page.number, "", graphic_bbox, 0.0, 1.0)
            label_match = re.match(r"^(Fig\.\s*\d+)", start.text)
            label = label_match.group(1) if label_match else "Figure"
            figures.append(
                ContentBlock(
                    page.number,
                    _join_lines(caption_boxes),
                    [graphic_box],
                    f"figure:{label}",
                    caption_boxes=caption_boxes,
                )
            )
            consumed.update(caption_boxes)
            consumed.update(box for box in prior if _intersection(box.bbox, graphic_bbox) > 0)
    return figures, consumed


def _table_omissions(pages: list[PageData]) -> tuple[list[dict[str, Any]], set[TextBox]]:
    omissions: list[dict[str, Any]] = []
    consumed: set[TextBox] = set()
    for page in pages:
        boxes = sorted(page.native, key=lambda box: (box.bbox[1], box.bbox[0]))
        for start in [box for box in boxes if re.match(r"^Table\s+\d+\b", box.text)]:
            candidates = [box for box in boxes if box.bbox[1] >= start.bbox[1]]
            table_boxes: list[TextBox] = []
            for box in candidates:
                if table_boxes and box.font_size >= 9.5 and box.bbox[1] - table_boxes[-1].bbox[3] > 0.015:
                    break
                table_boxes.append(box)
            if len(table_boxes) < 2:
                continue
            consumed.update(table_boxes)
            bbox = _bounded_bbox(
                min(box.bbox[0] for box in table_boxes),
                min(box.bbox[1] for box in table_boxes),
                max(box.bbox[2] for box in table_boxes),
                max(box.bbox[3] for box in table_boxes),
            )
            evidence = TextBox(page.number, start.text, bbox, start.font_size)
            omissions.append(
                _omission(
                    evidence,
                    "other",
                    "Table detected, but reliable cell structure cannot yet be reconstructed.",
                )
            )
    return omissions, consumed


def _box_area(box: TextBox) -> float:
    return (box.bbox[2] - box.bbox[0]) * (box.bbox[3] - box.bbox[1])


def _excluded_from_math(box: TextBox, regions: list[tuple[int, Iterable[float]]]) -> bool:
    area = _box_area(box)
    return any(
        page == box.page and area > 0 and _intersection(box.bbox, bbox) / area >= 0.2
        for page, bbox in regions
    )


def _natural_word_count(text: str) -> int:
    return sum(len(word) >= 3 for word in re.findall(r"[A-Za-z]+", text))


def _math_signal_count(text: str) -> int:
    return len(re.findall(r"[0-9=+−<>√Σ∆δφϕηµενπτ|⌊⌋]", text))


def _formula_only_fragment(text: str) -> bool:
    stripped = text.strip()
    if not stripped or _natural_word_count(stripped) or re.search(r"\(\d{4}\)", stripped):
        return False
    tokens = re.findall(r"[A-Za-z]+", stripped)
    single_letter_tokens = sum(len(token) <= 2 for token in tokens)
    symbol_only = re.fullmatch(r"[^A-Za-z0-9]+", stripped) is not None
    return _math_signal_count(stripped) > 0 or single_letter_tokens >= 2 or symbol_only


def _display_formula_omissions(
    pages: list[PageData],
    excluded_regions: list[tuple[int, Iterable[float]]] | None = None,
) -> tuple[list[dict[str, Any]], set[TextBox]]:
    """Detect display-math regions without claiming semantic completeness."""
    omissions: list[dict[str, Any]] = []
    consumed: set[TextBox] = set()
    excluded_regions = excluded_regions or []
    for page in pages:
        boxes = [
            box
            for box in sorted(page.native, key=lambda box: (box.bbox[1], box.bbox[0]))
            if not _excluded_from_math(box, excluded_regions)
        ]
        seeds = [
            box
            for box in boxes
            if "=" in box.text
            and _natural_word_count(box.text) <= 2
            and _math_signal_count(box.text) >= 3
            and box.bbox[2] - box.bbox[0] >= 0.1
        ]
        for seed in seeds:
            if seed in consumed:
                continue
            center = (seed.bbox[1] + seed.bbox[3]) / 2
            cluster = [
                box
                for box in boxes
                if abs((box.bbox[1] + box.bbox[3]) / 2 - center) <= 0.018
                and (_natural_word_count(box.text) <= 2 or box.font_size < seed.font_size * 0.85)
            ]
            if len(cluster) < 2:
                continue
            consumed.update(cluster)
            bbox = _bounded_bbox(
                min(box.bbox[0] for box in cluster),
                min(box.bbox[1] for box in cluster),
                max(box.bbox[2] for box in cluster),
                max(box.bbox[3] for box in cluster),
            )
            omissions.append(
                _omission(
                    TextBox(page.number, seed.text, bbox, seed.font_size),
                    "other",
                    "Detected display-math region; semantic formula recovery remains partial.",
                )
            )
    return omissions, consumed


def _append_text(
    blocks: list[ContentBlock], box: TextBox, kind: str = "paragraph", *, force_new: bool = False
) -> None:
    previous = blocks[-1] if blocks else None
    if not force_new and previous is not None and previous.kind == kind:
        last = previous.boxes[-1]
        same_page = last.page == box.page
        vertical_gap = box.bbox[1] - last.bbox[3] if same_page else box.bbox[1] + (1 - last.bbox[3])
        recent = previous.boxes[-4:]
        same_column = any(abs(candidate.bbox[0] - box.bbox[0]) < 0.08 for candidate in recent)
        same_baseline = same_page and abs(last.bbox[1] - box.bbox[1]) < 0.004
        expected_left = min(candidate.bbox[0] for candidate in recent)
        first_line_indent = same_page and box.bbox[0] > expected_left + 0.015 and not same_baseline
        if vertical_gap < 0.035 and (same_column or same_baseline) and not first_line_indent:
            previous.text = _join_lines([TextBox(box.page, previous.text, last.bbox), box])
            previous.boxes.append(box)
            return
    blocks.append(ContentBlock(box.page, box.text, [box], kind))


def _group_blocks(pages: list[PageData]) -> StructureResult:
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
    title_box = (
        max(first_candidates, key=lambda item: item[0])[1]
        if first_candidates
        else max(pages[0].native, key=lambda box: (box.font_size, len(box.text)))
    )
    title = ContentBlock(1, title_box.text, [title_box], "title", "title-000002")
    front, consumed = _front_matter(pages, title_box)
    figures, figure_consumed = _figure_blocks(pages)
    consumed.update(figure_consumed)
    table_omissions, table_consumed = _table_omissions(pages)
    consumed.update(table_consumed)
    math_exclusions: list[tuple[int, Iterable[float]]] = [
        (figure.page, figure.boxes[0].bbox) for figure in figures
    ]
    math_exclusions.extend((box.page, box.bbox) for figure in figures for box in figure.caption_boxes)
    math_exclusions.extend(
        (int(omission["physical_page"]), cast(list[float], omission["bbox"])) for omission in table_omissions
    )
    formula_omissions, formula_consumed = _display_formula_omissions(pages, math_exclusions)
    consumed.update(formula_consumed)
    figure_by_page_y = {
        (figure.page, round(figure.caption_boxes[0].bbox[1], 6)): figure for figure in figures
    }
    omissions: list[dict[str, Any]] = []
    omissions.extend(table_omissions)
    omissions.extend(formula_omissions)
    blocks: list[ContentBlock] = []
    references_started = False
    has_reference_heading = any(
        box.text.strip().casefold() == "references" for page in pages for box in page.native
    )
    references: list[ContentBlock] = []
    current_reference: ContentBlock | None = None
    paragraph_barrier = False
    for page in pages:
        ordered = _reading_order(page.native)
        if any(box.text.strip().casefold() == "references" for box in page.native):
            ordered = sorted(page.native, key=lambda box: (box.bbox[1], box.bbox[0]))
        font_sizes = [
            box.font_size
            for box in ordered
            if box not in consumed and box.font_size >= 8 and not _is_page_number(box)
        ]
        median_font = statistics.median(font_sizes) if font_sizes else 10.0
        prose_boxes = [
            box
            for box in ordered
            if box.font_size >= median_font * 0.85 and _natural_word_count(box.text) >= 1
        ]
        for box in ordered:
            if box in consumed:
                if box in formula_consumed or box in table_consumed:
                    paragraph_barrier = True
                figure = figure_by_page_y.get((box.page, round(box.bbox[1], 6)))
                if figure is not None:
                    blocks.append(figure)
                continue
            if _is_page_number(box):
                omissions.append(
                    _omission(box, "page-number", "Printed page number is not canonical article content.")
                )
                continue
            if _is_marginal_vertical_stamp(box):
                omissions.append(
                    _omission(
                        box,
                        "page-header",
                        "Repository side stamp is recorded as source metadata, not body text.",
                    )
                )
                continue
            overlay_math = bool(
                box.font_size < median_font * 0.8
                and any(
                    other is not box and min(box.bbox[3], other.bbox[3]) > max(box.bbox[1], other.bbox[1])
                    for other in prose_boxes
                )
            )
            if overlay_math or _formula_only_fragment(box.text):
                omissions.append(
                    _omission(
                        box,
                        "other",
                        "Isolated mathematical typesetting fragment cannot yet be reconstructed reliably.",
                    )
                )
                continue
            is_heading = (
                len(box.text) <= 140
                and box.font_size >= median_font * 1.15
                and bool(re.search(r"[A-Za-z]", box.text))
            )
            if box.text.strip().casefold() == "references":
                references_started = True
                references.append(ContentBlock(box.page, box.text.strip(), [box], "reference-heading"))
                current_reference = None
                continue
            if not has_reference_heading and not references_started and re.match(r"^\[\d+\]", box.text):
                references_started = True
            if references_started:
                if re.match(r"^\[\d+\]", box.text):
                    current_reference = ContentBlock(box.page, box.text, [box], "reference")
                    references.append(current_reference)
                elif current_reference is not None:
                    current_reference.text = _join_lines(
                        [TextBox(box.page, current_reference.text, current_reference.boxes[-1].bbox), box]
                    )
                    current_reference.boxes.append(box)
                else:
                    omissions.append(
                        _omission(box, "other", "Reference continuation could not be assigned safely.")
                    )
                continue
            if is_heading:
                paragraph_barrier = False
                kind = "heading:2" if re.match(r"^[A-Z]\d+\.", box.text) else "heading:1"
                previous_heading = blocks[-1] if blocks and blocks[-1].kind.startswith("heading:") else None
                if (
                    previous_heading is not None
                    and previous_heading.page == box.page
                    and box.bbox[1] - previous_heading.boxes[-1].bbox[3] < 0.02
                    and abs(box.font_size - previous_heading.boxes[-1].font_size) < 0.6
                ):
                    previous_heading.text = _join_lines([*previous_heading.boxes, box])
                    previous_heading.boxes.append(box)
                elif blocks and blocks[-1].kind == kind and blocks[-1].page == box.page:
                    blocks[-1].text = _join_lines([*blocks[-1].boxes, box])
                    blocks[-1].boxes.append(box)
                else:
                    blocks.append(ContentBlock(box.page, box.text, [box], kind))
                continue
            _append_text(blocks, box, force_new=paragraph_barrier)
            paragraph_barrier = False
    blocks.extend(references)
    _assign_ids(front, blocks)
    for number, omission in enumerate(omissions, 1):
        omission["id"] = f"omit-{number:06d}"
    return StructureResult(title, front, blocks, omissions)


def _assign_ids(front: FrontMatter, blocks: list[ContentBlock]) -> None:
    counters = {"paragraph": 0, "section": 0, "title": 2, "ref": 0, "fig": 0, "caption": 0}
    for number, author in enumerate(front.authors, 1):
        author.element_id = f"contrib-{number:06d}"
        author.child_ids = [f"name-{number:06d}"]
    for number, affiliation in enumerate(front.affiliations, 1):
        affiliation.element_id = f"aff-{number:06d}"
    if front.abstract:
        front.abstract.element_id = "abstract-000001"
        counters["paragraph"] += 1
        front.abstract.child_ids = [f"p-{counters['paragraph']:06d}"]
    if front.publication_date:
        front.publication_date.element_id = "pub-date-000001"
    if front.contribution_note:
        front.contribution_note.element_id = "fn-000001"
        counters["paragraph"] += 1
        front.contribution_note.child_ids = [f"p-{counters['paragraph']:06d}"]
    for block in blocks:
        if block.kind.startswith("heading:"):
            counters["section"] += 1
            block.element_id = f"sec-{counters['section']:06d}"
            counters["title"] += 1
            block.child_ids = [f"title-{counters['title']:06d}"]
        elif block.kind == "reference-heading":
            counters["title"] += 1
            block.element_id = f"title-{counters['title']:06d}"
        elif block.kind == "reference":
            counters["ref"] += 1
            block.element_id = f"ref-{counters['ref']:06d}"
        elif block.kind.startswith("figure:"):
            counters["fig"] += 1
            counters["caption"] += 1
            counters["paragraph"] += 1
            block.element_id = f"fig-{counters['fig']:06d}"
            block.child_ids = [
                f"caption-{counters['caption']:06d}",
                f"p-{counters['paragraph']:06d}",
            ]
        else:
            counters["paragraph"] += 1
            block.element_id = f"p-{counters['paragraph']:06d}"


def _build_xml(
    title: ContentBlock,
    front_matter: FrontMatter,
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
    if front_matter.authors:
        contrib_group = etree.SubElement(meta, "contrib-group")
        for author in front_matter.authors:
            contrib = etree.SubElement(
                contrib_group,
                "contrib",
                id=author.element_id,
                attrib={"contrib-type": "author"},
            )
            name = etree.SubElement(
                contrib,
                "name",
                id=author.child_ids[0],
                attrib={"name-style": "western"},
            )
            parts = author.text.rsplit(maxsplit=1)
            if len(parts) == 2:
                etree.SubElement(name, "surname").text = parts[1]
                etree.SubElement(name, "given-names").text = parts[0]
            else:
                etree.SubElement(name, "surname").text = author.text
            for rid in author.aff_rids:
                xref = etree.SubElement(contrib, "xref", attrib={"ref-type": "aff", "rid": rid})
                xref.text = str(int(rid.rsplit("-", 1)[1]))
            if author.equal_contribution and front_matter.contribution_note:
                xref = etree.SubElement(
                    contrib,
                    "xref",
                    attrib={"ref-type": "author-notes", "rid": front_matter.contribution_note.element_id},
                )
                xref.text = "†"
        for affiliation in front_matter.affiliations:
            aff = etree.SubElement(meta, "aff", id=affiliation.element_id)
            match = re.match(r"^(\d+)\s*(.*)$", affiliation.text)
            if match:
                etree.SubElement(aff, "label").text = match.group(1)
                aff[-1].tail = " " + match.group(2)
            else:
                aff.text = affiliation.text
        if front_matter.contribution_note:
            note = front_matter.contribution_note
            author_notes = etree.SubElement(meta, "author-notes")
            fn = etree.SubElement(
                author_notes,
                "fn",
                id=note.element_id,
                attrib={"fn-type": "equal"},
            )
            etree.SubElement(fn, "label").text = "†"
            note_text = re.sub(r"^†\s*", "", note.text)
            etree.SubElement(fn, "p", id=note.child_ids[0]).text = note_text
    if front_matter.publication_date:
        date = front_matter.publication_date
        _, day, month, year = date.kind.split(":")
        pub_date = etree.SubElement(
            meta,
            "pub-date",
            id=date.element_id,
            attrib={"date-type": "preprint", "publication-format": "electronic"},
        )
        etree.SubElement(pub_date, "day").text = day
        etree.SubElement(pub_date, "month").text = month
        etree.SubElement(pub_date, "year").text = year
    if front_matter.abstract:
        abstract_block = front_matter.abstract
        abstract = etree.SubElement(meta, "abstract", id=abstract_block.element_id)
        etree.SubElement(abstract, "p", id=abstract_block.child_ids[0]).text = abstract_block.text
    body = etree.SubElement(article, "body")
    current = body
    sections: dict[int, etree._Element] = {}
    back = etree.Element("back")
    ref_list: etree._Element | None = None
    for block in blocks:
        if block.kind.startswith("heading:"):
            level = int(block.kind.rsplit(":", 1)[1])
            parent = body if level == 1 or 1 not in sections else sections[1]
            current = etree.SubElement(parent, "sec", id=block.element_id)
            sections[level] = current
            for deeper in [value for value in sections if value > level]:
                del sections[deeper]
            heading = etree.SubElement(current, "title", id=block.child_ids[0])
            heading.text = block.text
        elif block.kind == "reference-heading":
            ref_list = etree.SubElement(back, "ref-list")
            etree.SubElement(ref_list, "title", id=block.element_id).text = block.text
        elif block.kind == "reference":
            if ref_list is None:
                ref_list = etree.SubElement(back, "ref-list")
            ref = etree.SubElement(ref_list, "ref", id=block.element_id)
            match = re.match(r"^\[(\d+)\]\s*(.*)$", block.text, re.DOTALL)
            if match:
                etree.SubElement(ref, "label").text = match.group(1)
                etree.SubElement(ref, "mixed-citation").text = match.group(2)
            else:
                etree.SubElement(ref, "mixed-citation").text = block.text
        elif block.kind.startswith("figure:"):
            figure = etree.SubElement(current, "fig", id=block.element_id)
            label = block.kind.split(":", 1)[1]
            etree.SubElement(figure, "label").text = label
            caption = etree.SubElement(figure, "caption", id=block.child_ids[0])
            caption_text = re.sub(r"^Fig\.\s*\d+\s*", "", block.text, count=1)
            etree.SubElement(caption, "p", id=block.child_ids[1]).text = caption_text
            etree.SubElement(
                figure,
                "graphic",
                attrib={f"{{{XLINK}}}href": f"../assets/content/figures/{block.element_id}.png"},
            )
        else:
            paragraph = etree.SubElement(current, "p", id=block.element_id)
            paragraph.text = block.text
    article.append(back)
    xml = etree.tostring(article, xml_declaration=True, encoding="UTF-8", pretty_print=False) + b"\n"

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
    if front_matter.authors:
        for author in front_matter.authors:
            xml_name = "".join(reversed(author.text.rsplit(maxsplit=1)))
            xref_text = "".join(str(int(rid.rsplit("-", 1)[1])) for rid in author.aff_rids)
            if author.equal_contribution:
                xref_text += "†"
            meta_blocks.append(
                ContentBlock(
                    author.page,
                    xml_name + xref_text,
                    author.boxes,
                    author.kind,
                    author.element_id,
                    child_ids=author.child_ids,
                    revision_before=author.revision_before,
                    aff_rids=author.aff_rids,
                    equal_contribution=author.equal_contribution,
                    derived_bbox=author.derived_bbox,
                )
            )
            meta_blocks.append(
                ContentBlock(
                    author.page,
                    xml_name,
                    author.boxes,
                    "name",
                    author.child_ids[0],
                    revision_before=author.revision_before,
                    derived_bbox=author.derived_bbox,
                )
            )
        meta_blocks.extend(front_matter.affiliations)
        if front_matter.contribution_note:
            note = front_matter.contribution_note
            meta_blocks.append(note)
            meta_blocks.append(
                ContentBlock(
                    note.page,
                    re.sub(r"^†\s*", "", note.text),
                    note.boxes,
                    "contribution-note-paragraph",
                    note.child_ids[0],
                )
            )
    if front_matter.publication_date:
        meta_blocks.append(front_matter.publication_date)
    if front_matter.abstract:
        meta_blocks.append(front_matter.abstract)
        meta_blocks.append(
            ContentBlock(
                front_matter.abstract.page,
                front_matter.abstract.text,
                front_matter.abstract.boxes,
                "abstract-paragraph",
                front_matter.abstract.child_ids[0],
            )
        )
    expanded: list[ContentBlock] = []
    for block in blocks:
        if block.kind.startswith("heading:"):
            expanded.append(ContentBlock(block.page, block.text, block.boxes, "section", block.element_id))
            expanded.append(ContentBlock(block.page, block.text, block.boxes, "title", block.child_ids[0]))
        elif block.kind.startswith("figure:"):
            expanded.append(block)
            expanded.append(
                ContentBlock(
                    block.page,
                    re.sub(r"^Fig\.\s*\d+\s*", "", block.text, count=1),
                    block.caption_boxes,
                    "caption",
                    block.child_ids[0],
                )
            )
            expanded.append(
                ContentBlock(
                    block.page,
                    re.sub(r"^Fig\.\s*\d+\s*", "", block.text, count=1),
                    block.caption_boxes,
                    "caption-paragraph",
                    block.child_ids[1],
                )
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


def _write_figure_assets(blocks: list[ContentBlock], pages: list[PageData], root: Path) -> None:
    page_by_number = {page.number: page for page in pages}
    for block in blocks:
        if not block.kind.startswith("figure:"):
            continue
        page = page_by_number[block.page]
        x0, y0, x1, y1 = block.boxes[0].bbox
        target = root / f"assets/content/figures/{block.element_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(page.image_path) as image:
            crop = image.convert("RGB").crop(
                (
                    round(x0 * image.width),
                    round(y0 * image.height),
                    round(x1 * image.width),
                    round(y1 * image.height),
                )
            )
            crop.save(target, format="PNG", dpi=(300, 300), icc_profile=image.info.get("icc_profile"))
        block.resources = [
            {
                "role": "normalized",
                "path": f"assets/content/figures/{block.element_id}.png",
                "media_type": "image/png",
                "sha256": _sha256(target),
                "source_id": "src-001",
            }
        ]


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
            source_record: dict[str, Any] = {
                "source_id": "src-001",
                "physical_page": page_number,
                "logical_page_id": f"lp-{page_number:06d}",
                "page_image": f"assets/evidence/pages/src-001/page-{page_number:06d}.png",
                "regions": [
                    {"bbox": list(box.bbox)} for box in sorted(boxes, key=lambda b: (b.bbox[1], b.bbox[0]))
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
            if block.kind.startswith("figure:"):
                source_record["candidates"] = []
            sources.append(source_record)
        revisions: list[dict[str, Any]] = []
        if block.derived_bbox is not None and block.revision_before is not None:
            evidence_box = block.boxes[0]
            revisions.append(
                {
                    "timestamp": revision_timestamp,
                    "actor": "software:paper2html-minimal-converter",
                    "method": "automatic",
                    "before": block.revision_before,
                    "after": block.text,
                    "reason": (
                        "Segmented one author from a source-native multi-author line using layout geometry; "
                        "the derived name bbox is approximate, while source regions retain the full "
                        "Poppler run."
                    ),
                    "evidence": [
                        {
                            "source_id": "src-001",
                            "physical_page": evidence_box.page,
                            "page_image": f"assets/evidence/pages/src-001/page-{evidence_box.page:06d}.png",
                            "bbox": list(evidence_box.bbox),
                        }
                    ],
                    "x-derived-bbox": list(block.derived_bbox),
                    "x-segmentation-method": "character-proportional-layout-interpolation",
                }
            )
        elif block.kind == "derived-issn":
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
        elif block.revision_before is not None:
            evidence_box = block.boxes[0]
            revisions.append(
                {
                    "timestamp": revision_timestamp,
                    "actor": "software:paper2html-minimal-converter",
                    "method": "automatic",
                    "before": block.revision_before,
                    "after": block.text,
                    "reason": (
                        "Parsed structured publication metadata from the source-visible repository stamp."
                    ),
                    "evidence": [
                        {
                            "source_id": "src-001",
                            "physical_page": evidence_box.page,
                            "page_image": f"assets/evidence/pages/src-001/page-{evidence_box.page:06d}.png",
                            "bbox": list(evidence_box.bbox),
                        }
                    ],
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
                **({"resources": block.resources} if block.resources else {}),
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


def _validate_remote_url(value: str) -> None:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConversionError("remote PDF URL must be absolute HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ConversionError("remote PDF URL must not contain credentials")
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ConversionError("remote PDF URL must not use localhost")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not _is_allowed_remote_address(literal):
        raise ConversionError(f"remote PDF URL must not use a non-global IP address: {literal}")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ConversionError("remote PDF exceeded the overall download deadline")
    return min(REMOTE_TIMEOUT_SECONDS, remaining)


def _global_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ConversionError(f"remote PDF resolved to an invalid IP address: {value}") from exc
    if not _is_allowed_remote_address(address):
        raise ConversionError(f"remote PDF must not resolve to a non-global IP address: {address}")
    return address


def _is_allowed_remote_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_reserved
    )


def _resolve_global_addresses(host: str, port: int, deadline: float) -> list[str]:
    result: queue.Queue[object] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except BaseException as exc:
            result.put(exc)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        resolved = result.get(timeout=_remaining(deadline))
    except queue.Empty as exc:
        raise ConversionError("remote PDF DNS resolution exceeded the overall deadline") from exc
    if isinstance(resolved, BaseException):
        raise ConversionError(f"cannot resolve remote PDF host: {resolved}") from resolved
    addresses: list[str] = []
    for item in cast(list[Any], resolved):
        candidate = item[4][0]
        address = _global_ip(candidate)
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise ConversionError("remote PDF host resolved to no addresses")
    return addresses


def _resolve_secure_dns(host: str, deadline: float) -> list[str]:
    addresses: list[str] = []
    for record_type in ("A", "AAAA"):
        raw: socket.socket | None = None
        connection: http.client.HTTPConnection | None = None
        try:
            raw = socket.create_connection((DOH_ADDRESS, 443), timeout=_remaining(deadline))
            peer = _global_ip(raw.getpeername()[0])
            if peer != ipaddress.ip_address(DOH_ADDRESS):
                raise ConversionError("secure DNS peer differs from its pinned global address")
            context = ssl.create_default_context(cafile=certifi.where())
            raw = context.wrap_socket(raw, server_hostname=DOH_HOST)
            raw.settimeout(_remaining(deadline))
            connection = http.client.HTTPConnection(DOH_HOST, 443, timeout=_remaining(deadline))
            connection.sock = raw
            query = urllib.parse.urlencode({"name": host, "type": record_type})
            connection.request(
                "GET",
                f"/dns-query?{query}",
                headers={
                    "Host": DOH_HOST,
                    "Accept": "application/dns-json",
                    "User-Agent": "Paper2HTML/0.1",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ConversionError(f"secure DNS returned HTTP {response.status}")
            payload = response.read(65537)
            _remaining(deadline)
            if len(payload) > 65536:
                raise ConversionError("secure DNS response exceeded the safety limit")
            value = json.loads(payload)
            if value.get("Status") != 0:
                raise ConversionError(f"secure DNS returned status {value.get('Status')}")
            expected_type = 1 if record_type == "A" else 28
            for answer in value.get("Answer", []):
                if answer.get("type") != expected_type:
                    continue
                normalized = str(_global_ip(str(answer.get("data", ""))))
                if normalized not in addresses:
                    addresses.append(normalized)
        except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException) as exc:
            raise ConversionError(f"secure DNS resolution failed: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()
            elif raw is not None:
                raw.close()
    if not addresses:
        raise ConversionError("secure DNS returned no A or AAAA addresses")
    return addresses


def _host_header(parsed: urllib.parse.SplitResult, port: int) -> str:
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    default = 443 if parsed.scheme == "https" else 80
    return host if port == default else f"{host}:{port}"


def _open_remote_response(
    url: str, deadline: float, secure_dns: bool = False
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    _validate_remote_url(url)
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ConversionError(f"remote PDF URL has an invalid port: {exc}") from exc
    addresses = (
        _resolve_secure_dns(host, deadline) if secure_dns else _resolve_global_addresses(host, port, deadline)
    )
    selected = addresses[0]
    raw: socket.socket | None = None
    connection: http.client.HTTPConnection | None = None
    try:
        raw = socket.create_connection((selected, port), timeout=_remaining(deadline))
        peer = _global_ip(raw.getpeername()[0])
        if peer != ipaddress.ip_address(selected):
            raise ConversionError("remote PDF connection peer differs from the validated DNS address")
        raw.settimeout(_remaining(deadline))
        if parsed.scheme == "https":
            context = ssl.create_default_context(cafile=certifi.where())
            raw = context.wrap_socket(raw, server_hostname=host)
            raw.settimeout(_remaining(deadline))
        connection = http.client.HTTPConnection(host, port, timeout=_remaining(deadline))
        connection.sock = raw
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        connection.request(
            "GET",
            target,
            headers={
                "Host": _host_header(parsed, port),
                "Accept": "application/pdf",
                "User-Agent": "Paper2HTML/0.1",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        return connection, response
    except Exception:
        if connection is not None:
            connection.close()
        elif raw is not None:
            raw.close()
        raise


def _descriptor(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
            raise ConversionError("source descriptor must be UTF-8 without BOM and use LF line endings")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversionError(f"cannot read source descriptor: {exc}") from exc
    required = {"format", "format_version", "case_id", "url", "sha256", "size", "original_name"}
    if not isinstance(value, dict) or set(value) != required:
        raise ConversionError(f"source descriptor fields must be exactly: {', '.join(sorted(required))}")
    if (
        value.get("format") != SOURCE_DESCRIPTOR_FORMAT
        or value.get("format_version") != SOURCE_DESCRIPTOR_VERSION
    ):
        raise ConversionError("unsupported source descriptor format or version")
    url = value.get("url")
    case_id = value.get("case_id")
    digest = value.get("sha256")
    size = value.get("size")
    original_name = value.get("original_name")
    if not isinstance(url, str):
        raise ConversionError("source descriptor url must be a string")
    if not isinstance(case_id, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", case_id) is None:
        raise ConversionError("source descriptor case_id must be a lowercase hyphenated identifier")
    _validate_remote_url(url)
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ConversionError("source descriptor sha256 must be 64 lowercase hexadecimal characters")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_REMOTE_PDF_BYTES:
        raise ConversionError(f"source descriptor size must be between 1 and {MAX_REMOTE_PDF_BYTES}")
    if (
        not isinstance(original_name, str)
        or not original_name.lower().endswith(".pdf")
        or Path(original_name).name != original_name
        or not original_name.strip()
    ):
        raise ConversionError("source descriptor original_name must be a plain PDF filename")
    return value


def _default_download_cache_dir() -> Path:
    cache_root = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_root) if cache_root else Path.home() / ".cache"
    return base / "paper2html" / "sources"


def _verified_cached_pdf(path: Path, digest: str, size: int) -> bool:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
            return False
        with path.open("rb") as stream:
            magic = stream.read(5)
        return magic == b"%PDF-" and _sha256(path) == digest
    except FileNotFoundError:
        return False


def _lock_cache(lock_path: Path, deadline: float) -> Any:
    lock = lock_path.open("a+b")
    while True:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except BlockingIOError:
            try:
                time.sleep(min(0.05, _remaining(deadline)))
            except Exception:
                lock.close()
                raise


def _download_pdf(
    descriptor: dict[str, Any], cache_dir: Path, allow_network: bool, secure_dns: bool = False
) -> tuple[Path, str]:
    digest = descriptor["sha256"]
    expected_size = descriptor["size"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{digest}.pdf"
    metadata = cache_dir / f"{digest}.origin.json"
    deadline = time.monotonic() + REMOTE_TOTAL_TIMEOUT_SECONDS
    lock = _lock_cache(cache_dir / f"{digest}.lock", deadline)
    temporary: Path | None = None
    try:
        if _verified_cached_pdf(target, digest, expected_size):
            final_url = descriptor["url"]
            if metadata.is_file() and not metadata.is_symlink():
                try:
                    cached_origin = json.loads(metadata.read_text(encoding="utf-8"))
                    if cached_origin.get("url") == descriptor["url"]:
                        candidate = cached_origin.get("final_url")
                        if isinstance(candidate, str):
                            _validate_remote_url(candidate)
                            final_url = candidate
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            return target, final_url
        target.unlink(missing_ok=True)
        if not allow_network:
            raise ConversionError("remote PDF is not in the verified cache; use --allow-network")
        url = descriptor["url"]
        redirects = 0
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        while True:
            connection, response = _open_remote_response(url, deadline, secure_dns)
            if response.status not in {301, 302, 303, 307, 308}:
                break
            location = response.getheader("Location")
            connection.close()
            if not location:
                raise ConversionError("remote PDF redirect has no Location header")
            redirects += 1
            if redirects > MAX_REMOTE_REDIRECTS:
                raise ConversionError(f"remote PDF exceeded {MAX_REMOTE_REDIRECTS} redirects")
            url = urllib.parse.urljoin(url, location)
            _validate_remote_url(url)
        if response is None or connection is None:
            raise ConversionError("remote PDF request produced no response")
        if response.status != 200:
            connection.close()
            raise ConversionError(f"remote PDF returned HTTP {response.status}")
        try:
            length = response.getheader("Content-Length")
            if length is not None and int(length) > min(expected_size, MAX_REMOTE_PDF_BYTES):
                raise ConversionError("remote PDF Content-Length exceeds the descriptor or safety limit")
            with tempfile.NamedTemporaryFile(
                "wb", dir=cache_dir, prefix=".download-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                total = 0
                first = b""
                while True:
                    _remaining(deadline)
                    if connection.sock is not None:
                        connection.sock.settimeout(_remaining(deadline))
                    chunk = response.read(min(1024 * 1024, expected_size - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected_size or total > MAX_REMOTE_PDF_BYTES:
                        raise ConversionError("remote PDF exceeds the descriptor or safety size limit")
                    if not first:
                        first = chunk[:5]
                    stream.write(chunk)
        finally:
            connection.close()
        if total != expected_size:
            raise ConversionError(f"remote PDF size mismatch: expected {expected_size}, got {total}")
        if first != b"%PDF-":
            raise ConversionError("remote response is not a PDF")
        actual = _sha256(temporary)
        if actual != digest:
            raise ConversionError(f"remote PDF SHA-256 mismatch: expected {digest}, got {actual}")
        os.replace(temporary, target)
        temporary = None
        final_url = url
        metadata_payload = (
            json.dumps(
                {"url": descriptor["url"], "final_url": final_url},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=cache_dir, prefix=".origin-", delete=False
        ) as metadata_stream:
            metadata_stream.write(metadata_payload)
            metadata_temporary = Path(metadata_stream.name)
        os.replace(metadata_temporary, metadata)
        return target, final_url
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise ConversionError(f"cannot download remote PDF: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def _resolve_pdf_input(input_path: Path, options: ConversionOptions) -> ResolvedPDF:
    path = input_path.resolve()
    if not path.is_file():
        raise ConversionError(f"input does not exist: {path}")
    if path.suffix.lower() == ".pdf":
        with path.open("rb") as stream:
            magic = stream.read(5)
        if magic != b"%PDF-":
            raise ConversionError("local PDF does not begin with the PDF magic header")
        return ResolvedPDF(path, path.name)
    descriptor = _descriptor(path)
    cache_dir = (options.download_cache_dir or _default_download_cache_dir()).resolve()
    downloaded, final_url = _download_pdf(descriptor, cache_dir, options.allow_network, options.secure_dns)
    return ResolvedPDF(downloaded, descriptor["original_name"], descriptor["url"], final_url)


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
        raise ConversionError("output must not equal or contain the input path")
    return resolved


def convert_pdf(pdf: Path, output: Path, options: ConversionOptions | None = None) -> dict[str, Any]:
    options = options or ConversionOptions()
    input_path = pdf.resolve()
    safe_output = _validate_output_path(input_path, output)
    resolved = _resolve_pdf_input(input_path, options)
    pdf = resolved.path
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
        structure = _group_blocks(pages)
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
            publication_box = structure.title.boxes[0]
            publication_sourced = False
        xml, addressable = _build_xml(
            structure.title,
            structure.front,
            structure.blocks,
            publication_id,
            publication_label,
            publication_box,
            publication_id_type,
            publication_sourced,
        )
        (root / "content").mkdir(parents=True)
        (root / "content/document.xml").write_bytes(xml)
        _write_figure_assets(structure.blocks, pages, root)
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
        _jsonl_dump(root / "provenance/omissions.jsonl", structure.omissions)
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
                    "original_name": resolved.original_name,
                    "media_type": "application/pdf",
                    "sha256": source_sha,
                    "size": pdf.stat().st_size,
                    "page_count": page_count,
                    "source_class": "born-digital",
                    "extraction_modes": ["native-pdf", "ocr"],
                    "embedded_path": None,
                    **(
                        {
                            "x-origin": {
                                "kind": "remote-url",
                                "url": resolved.source_url,
                                "final_url": resolved.final_url,
                                "sha256": source_sha,
                            }
                        }
                        if resolved.source_url is not None
                        else {}
                    ),
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
