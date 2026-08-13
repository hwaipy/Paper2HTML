from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from lxml import etree
from PIL import Image

from src.validator.validator import ADDRESSABLE_TAGS

ARTICLE = """<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" id="doc-000001"
 dtd-version="1.3" xml:lang="en" article-type="research-article">
 <front>
  <journal-meta>
   <journal-id id="journal-id-000001" journal-id-type="publisher-id">TEST</journal-id>
   <journal-title-group><journal-title id="title-000001">Test Journal</journal-title></journal-title-group>
   <issn id="issn-000001" pub-type="electronic">1234-5678</issn>
   <publisher><publisher-name>Test Publisher</publisher-name></publisher>
  </journal-meta>
  <article-meta>
   <article-id id="article-id-000001" pub-id-type="doi">10.0000/test</article-id>
   <title-group><article-title id="title-000002">Minimal Article</article-title></title-group>
   <pub-date id="pub-date-000001" date-type="pub" publication-format="electronic"><year>2026</year></pub-date>
   <abstract id="abstract-000001"><p id="p-000001">A short abstract.</p></abstract>
  </article-meta>
 </front>
 <body><sec id="sec-000001"><title id="title-000003">Introduction</title>
  <p id="p-000002">Hello world.</p></sec></body>
 <back><ref-list><ref id="ref-000001">
  <mixed-citation>Example reference.</mixed-citation></ref></ref-list></back>
</article>
"""


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", newline="\n")


def build_package(root: Path) -> tuple[Path, Path]:
    source_sha = "0" * 64
    package_id = uuid.uuid5(
        uuid.UUID("6d4d259c-105b-5fee-a87a-efd4ad4d9bf8"),
        f"paper2html-package:0.1:{source_sha}",
    )
    manifest = {
        "$schema": "https://hwaipy.github.io/Paper2HTML/schema/0.1/manifest.schema.json",
        "format": "paper2html-package",
        "format_version": "0.1",
        "package_id": f"urn:uuid:{package_id}",
        "created_at": "2026-08-12T00:00:00Z",
        "generator": {"name": "test", "version": "1.0"},
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
                "original_name": "test.pdf",
                "media_type": "application/pdf",
                "sha256": source_sha,
                "size": 1,
                "page_count": 1,
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
    dump(root / "manifest.json", manifest)
    (root / "content").mkdir()
    (root / "content/document.xml").write_text(ARTICLE, newline="\n")
    image_path = root / "assets/evidence/pages/src-001/page-000001.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (300, 300), "white").save(image_path, dpi=(300, 300))
    page = {
        "source_id": "src-001",
        "physical_page": 1,
        "logical_page_id": "lp-000001",
        "printed_label": None,
        "width_pt": 72.0,
        "height_pt": 72.0,
        "rotation_degrees": 0,
        "image": "assets/evidence/pages/src-001/page-000001.png",
        "image_width_px": 300,
        "image_height_px": 300,
        "render_dpi": 300,
        "ocr_status": "completed",
    }
    (root / "provenance").mkdir()
    (root / "provenance/pages.jsonl").write_text(json.dumps(page) + "\n")
    tree = etree.fromstring(ARTICLE.encode())
    addressable = [
        element for element in tree.iter() if element.tag in ADDRESSABLE_TAGS and element.get("id")
    ]
    records = []
    for order, element in enumerate(addressable, 1):
        element_id = element.get("id")
        candidates = [
            {
                "method": "native-pdf",
                "engine": "test",
                "engine_version": "1",
                "text": "".join(element.itertext()),
                "confidence": 1.0,
            },
            {
                "method": "ocr",
                "engine": "test",
                "engine_version": "1",
                "text": "".join(element.itertext()),
                "confidence": 1.0,
            },
        ]
        records.append(
            {
                "element_id": element_id,
                "xml_path": f"//*[@id='{element_id}']",
                "reading_order": order,
                "sources": [
                    {
                        "source_id": "src-001",
                        "physical_page": 1,
                        "logical_page_id": "lp-000001",
                        "page_image": page["image"],
                        "regions": [{"bbox": [0.1, 0.1, 0.9, 0.9]}],
                        "candidates": candidates,
                    }
                ],
                "decision": {"method": "native-pdf", "confidence": 1.0},
                "revisions": [],
            }
        )
    (root / "provenance/elements.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (root / "provenance/omissions.jsonl").write_text("")
    report = {
        "$schema": "https://hwaipy.github.io/Paper2HTML/schema/0.1/validation-report.schema.json",
        "format": "paper2html-validation-report",
        "format_version": "0.1",
        "valid": False,
        "validated_at": "2026-08-12T00:00:00Z",
        "checks": {
            name: "partial"
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
        "errors": [{"code": "not_yet_validated", "message": "Fixture placeholder."}],
        "warnings": [],
    }
    dump(root / "validation/report.json", report)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "checksums.sha256")
    (root / "checksums.sha256").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}\n"
            for path in files
        )
    )
    evidence = root.parent / "coverage.json"
    dump(
        evidence,
        {
            "engine": "test-auditor",
            "engine_version": "1",
            "reviewed_pages": [{"source_id": "src-001", "physical_page": 1}],
            "uncovered_objects": [],
        },
    )
    return root, evidence


@pytest.fixture
def package(tmp_path: Path) -> tuple[Path, Path]:
    return build_package(tmp_path / "package")
