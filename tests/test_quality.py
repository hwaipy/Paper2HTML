from __future__ import annotations

from pathlib import Path
from typing import Any

from lxml import etree

from src.converter import quality


def _record(element_id: str, text: str) -> dict[str, Any]:
    return {
        "element_id": element_id,
        "sources": [
            {
                "candidates": [
                    {"method": "native-pdf", "text": text},
                    {"method": "ocr", "text": text},
                ]
            }
        ],
        "revisions": [],
    }


def test_candidate_check_detects_rehashed_semantic_tampering() -> None:
    tree = etree.ElementTree(
        etree.fromstring(
            b"<article><front><article-meta><title-group>"
            b'<article-title id="title-000001">Fabricated title</article-title>'
            b'</title-group><contrib-group><contrib id="contrib-000001">Invented Author</contrib>'
            b'</contrib-group><abstract id="abstract-000001">Invented abstract</abstract>'
            b'</article-meta></front><body><p id="p-000001">Fabricated body</p></body>'
            b'<back><ref-list><ref id="ref-000001">Invented reference</ref></ref-list></back></article>'
        )
    )
    records = [
        _record("title-000001", "Original title"),
        _record("contrib-000001", "Original Author"),
        _record("abstract-000001", "Original abstract"),
        _record("p-000001", "Original body"),
        _record("ref-000001", "Original reference"),
    ]
    findings = quality._candidate_consistency(
        tree,
        records,
    )
    assert {item["element_id"] for item in findings} == {
        "title-000001",
        "contrib-000001",
        "abstract-000001",
        "p-000001",
        "ref-000001",
    }


def test_resource_check_rejects_corrupt_png(tmp_path: Path) -> None:
    path = tmp_path / "figure.png"
    path.write_bytes(b"not a PNG")
    findings = quality._resource_findings(
        tmp_path,
        [{"element_id": "fig-000001", "resources": [{"path": "figure.png"}]}],
    )
    assert findings == [{"code": "resource_unreadable", "path": "figure.png"}]


def test_candidate_check_combines_cross_page_evidence() -> None:
    tree = etree.ElementTree(
        etree.fromstring(b'<article><body><p id="p-000001">first half second half</p></body></article>')
    )
    record = _record("p-000001", "first half")
    record["sources"].append({"candidates": [{"method": "native-pdf", "text": "second half"}]})
    assert quality._candidate_consistency(tree, [record]) == []


def test_quality_cli_exit_codes(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(
        quality,
        "build_quality_report",
        lambda *_args, **_kwargs: {"status": "failed", "criteria": {}},
    )
    assert quality.main([str(tmp_path)]) == 1

    def broken(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise quality.QualityError("cannot run validator")

    monkeypatch.setattr(quality, "build_quality_report", broken)
    assert quality.main([str(tmp_path)]) == 2
