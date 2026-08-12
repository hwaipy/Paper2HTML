from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from converter import pipeline
from converter.pipeline import ConversionError, ConversionOptions, TextBox


def _fake_engines(monkeypatch: Any) -> None:
    monkeypatch.setattr(pipeline, "_pdf_metadata", lambda _: (1, [(72.0, 72.0)], [0]))

    def render(_: Path, destination: Path, __: int) -> list[Path]:
        destination.mkdir(parents=True)
        image = destination / "page-000001.png"
        Image.new("RGB", (300, 300), "white").save(image, dpi=(300, 300))
        return [image]

    boxes = [
        TextBox(1, "Minimal Real Title", (0.1, 0.1, 0.8, 0.2), 24.0),
        TextBox(1, "arXiv:2503.17744v1", (0.05, 0.02, 0.3, 0.05), 8.0),
        TextBox(1, "Body extracted from the PDF.", (0.1, 0.3, 0.8, 0.35), 10.0),
    ]
    ocr = [TextBox(1, box.text, box.bbox, confidence=0.9) for box in boxes]
    monkeypatch.setattr(pipeline, "_render_pages", render)
    monkeypatch.setattr(pipeline, "_extract_native", lambda *_: [boxes])
    monkeypatch.setattr(pipeline, "_extract_vision", lambda *_: [ocr])
    monkeypatch.setattr(pipeline, "_poppler_version", lambda: "test")
    monkeypatch.setattr(pipeline, "_vision_version", lambda: "test")

    class FakeResult:
        operational_error = False

        def __init__(self) -> None:
            self.report = {
                "$schema": pipeline.REPORT_SCHEMA,
                "format": "paper2html-validation-report",
                "format_version": "0.1",
                "valid": False,
                "validated_at": "2026-08-12T00:00:00Z",
                "checks": {
                    "manifest_schema": "passed",
                    "xml_well_formed": "passed",
                    "jats_bits_schema": "passed",
                    "p2h_profile": "passed",
                    "id_uniqueness": "passed",
                    "cross_references": "passed",
                    "page_coverage": "partial",
                    "element_provenance": "passed",
                    "asset_integrity": "passed",
                    "checksum_integrity": "passed",
                },
                "errors": [{"code": "page_coverage_not_audited", "message": "Not audited."}],
                "warnings": [],
            }

    def write_report(root: Path, result: FakeResult) -> None:
        report = root / "validation/report.json"
        report.write_text(json.dumps(result.report) + "\n")
        entries = {
            line.split("  ", 1)[1]: line.split("  ", 1)[0]
            for line in (root / "checksums.sha256").read_text().splitlines()
        }
        entries["validation/report.json"] = hashlib.sha256(report.read_bytes()).hexdigest()
        (root / "checksums.sha256").write_text(
            "".join(f"{digest}  {path}\n" for path, digest in sorted(entries.items()))
        )

    fake_validator = SimpleNamespace(
        ValidationOptions=lambda **_: object(),
        validate_package=lambda *_args, **_kwargs: FakeResult(),
        write_report=write_report,
    )
    monkeypatch.setattr(pipeline.importlib, "import_module", lambda _: fake_validator)


def test_conversion_builds_stable_package_skeleton(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_engines(monkeypatch)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\nminimal-test\n")
    output = tmp_path / "result"
    report = pipeline.convert_pdf(
        source,
        output,
        ConversionOptions(created_at="2026-08-12T00:00:00Z"),
    )
    assert report["checks"]["page_coverage"] == "partial"
    assert (output / "content/document.xml").is_file()
    assert (output / "assets/evidence/pages/src-001/page-000001.png").is_file()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["sources"][0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["created_at"] == "2026-08-12T00:00:00Z"
    stored_report = json.loads((output / "validation/report.json").read_text())
    assert stored_report["validated_at"] == "2026-08-12T00:00:00Z"
    elements = [json.loads(line) for line in (output / "provenance/elements.jsonl").read_text().splitlines()]
    assert elements
    assert all(
        {"native-pdf", "ocr"} <= {c["method"] for c in e["sources"][0]["candidates"]} for e in elements
    )
    by_id = {element["element_id"]: element for element in elements}
    arxiv_bbox = [0.05, 0.02, 0.3, 0.05]
    for element_id in (
        "journal-id-000001",
        "title-000001",
        "issn-000001",
        "article-id-000001",
    ):
        assert by_id[element_id]["sources"][0]["regions"] == [{"bbox": arxiv_bbox}]
    issn = by_id["issn-000001"]
    assert issn["sources"][0]["candidates"][0]["text"] == "arXiv:2503.17744v1"
    assert issn["revisions"][0]["before"] == ""
    assert issn["revisions"][0]["after"] == "2331-8422"
    assert issn["revisions"][0]["x-registry"].startswith("https://portal.issn.org/")


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_fixed_timestamp_makes_complete_package_deterministic(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_engines(monkeypatch)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\ndeterministic\n")
    options = ConversionOptions(created_at="2026-08-12T00:00:00Z")
    first, second = tmp_path / "first", tmp_path / "second"
    pipeline.convert_pdf(source, first, options)
    pipeline.convert_pdf(source, second, options)
    assert _tree_bytes(first) == _tree_bytes(second)


def test_unsourced_generic_metadata_does_not_claim_title_bbox(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_engines(monkeypatch)
    boxes = [
        TextBox(1, "Generic PDF Title", (0.1, 0.1, 0.8, 0.2), 24.0),
        TextBox(1, "Body text.", (0.1, 0.3, 0.8, 0.35), 10.0),
    ]
    monkeypatch.setattr(pipeline, "_extract_native", lambda *_: [boxes])
    monkeypatch.setattr(
        pipeline,
        "_extract_vision",
        lambda *_: [[TextBox(1, box.text, box.bbox, confidence=0.9) for box in boxes]],
    )
    source = tmp_path / "filename-is-not-metadata.pdf"
    source.write_bytes(b"%PDF")
    output = tmp_path / "result"
    pipeline.convert_pdf(
        source,
        output,
        ConversionOptions(created_at="2026-08-12T00:00:00Z"),
    )
    ids = {
        json.loads(line)["element_id"]
        for line in (output / "provenance/elements.jsonl").read_text().splitlines()
    }
    assert not {"journal-id-000001", "title-000001", "article-id-000001"} & ids
    assert "Unknown publication" in (output / "content/document.xml").read_text()


def test_existing_output_is_not_touched_without_replace(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    output = tmp_path / "result"
    output.mkdir()
    marker = output / "mine.txt"
    marker.write_text("keep")
    try:
        pipeline.convert_pdf(source, output)
    except ConversionError as exc:
        assert "--replace" in str(exc)
    else:
        raise AssertionError("existing output should be rejected")
    assert marker.read_text() == "keep"


def test_replace_succeeds_atomically(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_engines(monkeypatch)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    output = tmp_path / "result"
    output.mkdir()
    (output / "old.txt").write_text("old")
    pipeline.convert_pdf(
        source,
        output,
        ConversionOptions(created_at="2026-08-12T00:00:00Z", replace=True),
    )
    assert not (output / "old.txt").exists()
    assert (output / "manifest.json").is_file()


def test_replace_rolls_back_when_publish_fails(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_engines(monkeypatch)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    output = tmp_path / "result"
    output.mkdir()
    marker = output / "old.txt"
    marker.write_text("old")
    real_replace = pipeline.os.replace
    calls = 0

    def fail_second(source_path: os.PathLike[str], target_path: os.PathLike[str]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publish failure")
        real_replace(source_path, target_path)

    monkeypatch.setattr(pipeline.os, "replace", fail_second)
    with pytest.raises(OSError, match="simulated"):
        pipeline.convert_pdf(
            source,
            output,
            ConversionOptions(created_at="2026-08-12T00:00:00Z", replace=True),
        )
    assert marker.read_text() == "old"


@pytest.mark.parametrize("kind", ["same", "ancestor"])
def test_output_cannot_equal_or_contain_input(tmp_path: Path, kind: str) -> None:
    case = tmp_path / "case"
    case.mkdir()
    source = case / "paper.pdf"
    source.write_bytes(b"%PDF")
    output = source if kind == "same" else case
    with pytest.raises(ConversionError, match="input PDF"):
        pipeline.convert_pdf(source, output, ConversionOptions(replace=True))
    assert source.read_bytes() == b"%PDF"


def test_output_symlink_and_symlinked_parent_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep")
    output_link = tmp_path / "output-link"
    output_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ConversionError, match="symbolic links"):
        pipeline.convert_pdf(source, output_link, ConversionOptions(replace=True))
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ConversionError, match="symbolic links"):
        pipeline.convert_pdf(source, parent_link / "new-output")
    assert marker.read_text() == "keep"


def test_reading_order_prefers_left_column_before_right() -> None:
    boxes = [
        TextBox(1, "right one", (0.55, 0.2, 0.9, 0.25)),
        TextBox(1, "right two", (0.55, 0.3, 0.9, 0.35)),
        TextBox(1, "left one", (0.1, 0.3, 0.45, 0.35)),
        TextBox(1, "left two", (0.1, 0.4, 0.45, 0.45)),
    ]
    assert [box.text for box in pipeline._reading_order(boxes)] == [
        "left one",
        "left two",
        "right one",
        "right two",
    ]


def test_reference_boundaries_and_order_are_preserved(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    Image.new("RGB", (300, 300), "white").save(image)
    boxes = [
        TextBox(1, "Title", (0.1, 0.01, 0.8, 0.05), 20),
        TextBox(1, "224011 (2012)", (0.2, 0.09, 0.3, 0.10), 10),
        TextBox(1, "[53] https://example.test", (0.16, 0.12, 0.54, 0.13), 10),
        TextBox(1, "[54] First reference sending-", (0.16, 0.14, 0.79, 0.15), 10),
        TextBox(1, "or-not-sending continuation.", (0.20, 0.16, 0.79, 0.17), 10),
        TextBox(1, "[55] Second reference min-", (0.16, 0.20, 0.79, 0.21), 10),
        TextBox(1, "and max-entropies", (0.20, 0.22, 0.79, 0.23), 10),
        TextBox(1, "(2013)", (0.20, 0.24, 0.25, 0.25), 10),
    ]
    page = pipeline.PageData(1, 72, 72, 0, image, 300, 300, boxes, boxes)
    _, blocks = pipeline._group_blocks([page])
    texts = [block.text for block in blocks]
    assert texts == [
        "224011 (2012)",
        "[53] https://example.test",
        "[54] First reference sending-or-not-sending continuation.",
        "[55] Second reference min- and max-entropies (2013)",
    ]


def test_vertical_margin_stamp_cannot_become_article_title(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    Image.new("RGB", (300, 300), "white").save(image)
    stamp = TextBox(
        1,
        "Repository:1234.56789v1 [subject] 1 Jan 2026",
        (0.04, 0.18, 0.08, 0.86),
        30,
    )
    real_title = TextBox(1, "A General Horizontal Document Title", (0.2, 0.12, 0.8, 0.17), 17)
    body = TextBox(1, "Ordinary body text.", (0.2, 0.3, 0.8, 0.35), 10)
    page = pipeline.PageData(
        1,
        72,
        72,
        0,
        image,
        300,
        300,
        [stamp, real_title, body],
        [stamp, real_title, body],
    )
    title, blocks = pipeline._group_blocks([page])
    assert title.text == real_title.text
    body_texts = [block.text for block in blocks]
    assert real_title.text not in body_texts
    assert stamp.text not in body_texts
    assert body.text in body_texts
