from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lxml import etree

from converter.golden import _canonical_sha, _element_projection, _sha256_bytes

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
    assert projection["element_count"] == len(elements) == 397
    assert projection["omission_count"] == len(omissions) == 0
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
    xml = (GOLDEN / "expected/content/document.xml").read_text(encoding="utf-8")
    assert xml.index("224011 (2012)") < xml.index("[53] https://github.com/hwaipy/InteractionFreePy")


def test_golden_expected_is_explicitly_not_a_complete_package() -> None:
    expected = GOLDEN / "expected"
    assert not (expected / "assets/evidence/pages").exists()
    assert not (expected / "checksums.sha256").exists()
