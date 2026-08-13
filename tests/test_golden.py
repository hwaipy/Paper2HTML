from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lxml import etree

from src.converter.golden import _canonical_sha, _element_projection, _sha256_bytes

GOLDEN = Path("tests/golden/arxiv-2503-17744v1")


def _records(relative: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (GOLDEN / "expected" / relative).read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_committed_golden_projection_is_self_consistent() -> None:
    projection = json.loads((GOLDEN / "projection.json").read_text(encoding="utf-8"))
    descriptor = json.loads((GOLDEN / "source.json").read_text(encoding="utf-8"))
    manifest = json.loads((GOLDEN / "expected/manifest.json").read_text(encoding="utf-8"))
    report = json.loads((GOLDEN / "expected/validation/report.json").read_text(encoding="utf-8"))
    quality = json.loads((GOLDEN / "quality.json").read_text(encoding="utf-8"))
    pages = _records("provenance/pages.jsonl")
    elements = _records("provenance/elements.jsonl")
    omissions = _records("provenance/omissions.jsonl")

    assert descriptor["case_id"] == GOLDEN.name
    assert descriptor["url"] == "https://arxiv.org/pdf/2503.17744v1"
    assert descriptor["sha256"] == manifest["sources"][0]["sha256"]
    assert descriptor["size"] == manifest["sources"][0]["size"]
    assert manifest["sources"][0]["x-origin"]["url"] == descriptor["url"]
    assert projection["notice"].endswith("not a complete or conforming P2H Package.")
    assert projection["source"] == manifest["sources"][0]
    assert projection["package_id"] == manifest["package_id"]
    assert projection["page_count"] == len(pages) == 17
    assert projection["element_count"] == len(elements) == 167
    assert projection["omission_count"] == len(omissions) == 97
    assert projection["content_quality"] == quality
    assert len(projection["page_images"]) == 17
    assert projection["validation"]["valid"] is False
    assert projection["validation"]["checks"]["page_coverage"] == "partial"
    assert projection["validation"]["error_codes"] == ["page_coverage_not_audited"]
    assert report["errors"][0]["code"] == "page_coverage_not_audited"
    assert projection["document_sha256"] == _sha256_bytes(
        (GOLDEN / "expected/content/document.xml").read_bytes()
    )
    assert projection["pages_sha256"] == _canonical_sha(pages)
    assert projection["elements_sha256"] == _canonical_sha(
        [_element_projection(record) for record in elements]
    )
    assert projection["omissions_sha256"] == _canonical_sha(omissions)


def test_committed_golden_captures_semantic_and_spatial_anchors() -> None:
    tree = etree.parse(str(GOLDEN / "expected/content/document.xml"))
    title = tree.xpath("string(//*[@id='title-000002'])")
    assert title == "Free-Space Twin-Field Quantum Key Distribution"
    assert len(tree.xpath("//contrib[@contrib-type='author']")) == 25
    assert len(tree.xpath("//article-meta/aff")) == 6
    assert len(tree.xpath("//contrib/xref[@ref-type='aff']")) == 64
    assert tree.xpath("normalize-space(string(//author-notes/fn[@fn-type='equal']))") == (
        "†These authors contributed equally to this work."
    )
    assert len(tree.xpath("//article-meta/abstract")) == 1
    assert tree.xpath("normalize-space(string(//article-meta/pub-date))") == "2232025"
    assert [int(value) for value in tree.xpath("//ref/label/text()")] == list(range(1, 56))
    assert len(tree.xpath("//fig[caption and graphic]")) == 3
    assert tree.xpath("normalize-space(string(//sec/title))") == "Acknowledgments"
    elements = _records("provenance/elements.jsonl")
    assert [record["reading_order"] for record in elements] == list(range(1, len(elements) + 1))
    by_id = {record["element_id"]: record for record in elements}
    title_source = by_id["title-000002"]["sources"][0]
    assert title_source["physical_page"] == 1
    assert title_source["regions"]
    assert {candidate["method"] for candidate in title_source["candidates"]} == {
        "native-pdf",
        "ocr",
    }
    assert by_id["ref-000055"]["sources"][-1]["physical_page"] == 17
    assert by_id["fig-000001"]["resources"][0]["path"].endswith("fig-000001.png")
    omissions = _records("provenance/omissions.jsonl")
    assert sum(record["type"] == "page-number" for record in omissions) == 17


def test_committed_content_quality_report_is_explicit_and_machine_checkable() -> None:
    quality = json.loads((GOLDEN / "quality.json").read_text(encoding="utf-8"))
    assert quality["status"] == "partial"
    assert quality["metrics"] == {
        "pages": 17,
        "addressable_elements": 167,
        "article_titles": 1,
        "authors": 25,
        "author_affiliation_xrefs": 64,
        "equal_contribution_notes": 1,
        "affiliations": 6,
        "abstracts": 1,
        "publication_dates": 1,
        "body_sections": 4,
        "body_paragraphs": 31,
        "references": 55,
        "figures": 3,
        "tables": 0,
        "formulas": 0,
        "display_math_regions_omitted_with_evidence": 2,
        "tables_omitted_with_evidence": 1,
        "omissions": 97,
        "page_number_omissions": 17,
        "page_number_paragraphs": 0,
        "short_fragment_paragraphs": 0,
        "formula_only_paragraphs": 0,
    }
    assert quality["criteria"]["figures_tables_formulas"]["status"] == "partial"
    for name in ("front_matter", "semantic_structure", "references", "non_body_classification"):
        assert quality["criteria"][name]["status"] == "partial"
    for name in ("package_integrity", "xml_provenance_consistency", "resource_integrity"):
        assert quality["criteria"][name]["status"] == "passed"


def test_golden_expected_is_explicitly_not_a_complete_package() -> None:
    expected = GOLDEN / "expected"
    assert not (expected / "assets/evidence/pages").exists()
    assert not (expected / "checksums.sha256").exists()
