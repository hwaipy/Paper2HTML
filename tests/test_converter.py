from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from src.converter import golden as golden_module
from src.converter import pipeline
from src.converter.golden import GoldenError, build_projection, compare_projection, update_golden
from src.converter.models import EngineInfo, PDFInfo, RenderedPage, TextExtractionResult
from src.converter.pipeline import ConversionError, ConversionOptions, TextBox
from src.converter.tool_interfaces import ConversionTools


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


def test_reading_order_respects_visual_bands_and_columns() -> None:
    boxes = [
        TextBox(1, "full-width figure caption", (0.1, 0.30, 0.9, 0.34), 10),
        TextBox(1, "left first", (0.1, 0.40, 0.45, 0.44), 10),
        TextBox(1, "left second", (0.1, 0.50, 0.45, 0.54), 10),
        TextBox(1, "right first", (0.55, 0.40, 0.9, 0.44), 10),
        TextBox(1, "right second", (0.55, 0.50, 0.9, 0.54), 10),
    ]
    assert [box.text for box in pipeline._reading_order(boxes)] == [
        "full-width figure caption",
        "left first",
        "left second",
        "right first",
        "right second",
    ]


def test_line_joining_preserves_compounds_and_repairs_clear_suffix_breaks() -> None:
    def joined(left: str, right: str) -> str:
        return pipeline._join_lines(
            [TextBox(1, left, (0.1, 0.1, 0.8, 0.2)), TextBox(1, right, (0.1, 0.2, 0.8, 0.3))]
        )

    assert joined("atmospheric turbulence-", "induced distortion") == (
        "atmospheric turbulence-induced distortion"
    )
    assert joined("an open-", "channel protocol") == "an open-channel protocol"
    assert joined("a free-", "space channel") == "a free-space channel"
    assert joined("a mile-", "stone result") == "a milestone result"
    assert joined("the pho-", "tons arrived") == "the photons arrived"
    assert joined("a measure-", "ment result") == "a measurement result"
    assert joined("min-", "and max-entropies") == "min- and max-entropies"


def test_inrun_soft_hyphens_use_language_evidence() -> None:
    assert pipeline._repair_token_spacing("Entan-gling independent photons") == (
        "Entangling independent photons"
    )
    assert pipeline._repair_token_spacing("the repeater-less bound") == "the repeaterless bound"
    for compound in (
        "turbulence-induced",
        "open-channel",
        "free-space",
        "rate-distance",
        "finite-key",
        "Satellite-to-ground",
    ):
        assert pipeline._repair_token_spacing(compound) == compound


def test_display_formula_is_one_complete_omission() -> None:
    page = pipeline.PageData(1, 100, 100, 0, Path("unused"), 100, 100)
    prose_before = TextBox(1, "regarded as", (0.2, 0.25, 0.4, 0.27), 10)
    pieces = [
        TextBox(1, "δφ = 2πτδν +", (0.4, 0.28, 0.7, 0.30), 10),
        TextBox(1, "∆L(τ)", (0.55, 0.27, 0.65, 0.29), 10),
        TextBox(1, "(1)", (0.8, 0.28, 0.84, 0.30), 10),
    ]
    prose_after = TextBox(1, "where the channel fluctuates", (0.2, 0.31, 0.8, 0.33), 10)
    page.native = [prose_before, *pieces, prose_after]
    omissions, consumed = pipeline._display_formula_omissions([page])
    assert len(omissions) == 1
    assert consumed == set(pieces)
    assert prose_before not in consumed and prose_after not in consumed


def test_display_formula_detection_excludes_figure_overlap() -> None:
    page = pipeline.PageData(1, 100, 100, 0, Path("unused"), 100, 100)
    chart_label = TextBox(1, "QBER = 0.14", (0.55, 0.25, 0.75, 0.27), 10)
    chart_tick = TextBox(1, "10 −7", (0.30, 0.27, 0.36, 0.29), 8)
    page.native = [chart_label, chart_tick]
    omissions, consumed = pipeline._display_formula_omissions([page], [(1, (0.1, 0.1, 0.9, 0.5))])
    assert omissions == []
    assert consumed == set()


def test_formula_only_fragments_and_prose_are_distinguished() -> None:
    for fragment in ("− | −", "v x y z", "C (k)−C (k)", "×", "{", "∼"):
        assert pipeline._formula_only_fragment(fragment)
    for prose in (
        "where R is the key rate",
        "encoding satisfies 1 cos(φ + ∆φ(t)) < Λ",
        "224011 (2012)",
    ):
        assert not pipeline._formula_only_fragment(prose)


def test_formula_omission_forces_paragraph_boundary() -> None:
    blocks: list[pipeline.ContentBlock] = []
    before = TextBox(1, "approximatively regarded as", (0.2, 0.2, 0.8, 0.22), 10)
    after = TextBox(1, "where the channel fluctuates", (0.2, 0.28, 0.8, 0.30), 10)
    pipeline._append_text(blocks, before)
    pipeline._append_text(blocks, after, force_new=True)
    assert [block.text for block in blocks] == [before.text, after.text]


def _write_descriptor(path: Path, source: bytes) -> dict[str, Any]:
    value = {
        "format": "paper2html-pdf-source",
        "format_version": "1",
        "case_id": "paper-v1",
        "url": "https://example.test/papers/paper-v1.pdf",
        "sha256": hashlib.sha256(source).hexdigest(),
        "size": len(source),
        "original_name": "paper-v1.pdf",
    }
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return value


class _RemoteResponse:
    def __init__(
        self,
        payload: bytes = b"",
        *,
        status: int = 200,
        location: str | None = None,
        read_delay: float = 0.0,
    ) -> None:
        self.payload = payload
        self.status = status
        self.location = location
        self.read_delay = read_delay

    def getheader(self, name: str) -> str | None:
        if name == "Location":
            return self.location
        if name == "Content-Length" and self.status == 200:
            return str(len(self.payload))
        return None

    def read(self, _: int) -> bytes:
        if self.read_delay:
            time.sleep(self.read_delay)
        result, self.payload = self.payload, b""
        return result


class _RemoteConnection:
    sock = None

    def close(self) -> None:
        pass


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


def test_conversion_accepts_replacement_tools_and_records_their_engines(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _fake_engines(monkeypatch)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\nreplacement-tools\n")
    boxes = [
        TextBox(1, "Replacement Tool Title", (0.1, 0.1, 0.8, 0.2), 24.0),
        TextBox(1, "arXiv:2503.17744v1", (0.05, 0.02, 0.3, 0.05), 8.0),
        TextBox(1, "Body from a replacement extractor.", (0.1, 0.3, 0.8, 0.35), 10.0),
    ]

    def render(_: Path, destination: Path, __: PDFInfo) -> list[RenderedPage]:
        destination.mkdir(parents=True)
        image_path = destination / "page-000001.png"
        Image.new("RGB", (300, 300), "white").save(image_path, dpi=(300, 300))
        return [RenderedPage(1, image_path, 300, 300)]

    tools = ConversionTools(
        inspector=SimpleNamespace(inspect=lambda _: PDFInfo(1, [(72.0, 72.0)], [0])),
        renderer=SimpleNamespace(render=render),
        native_text=SimpleNamespace(
            extract=lambda *_: TextExtractionResult(
                EngineInfo("replacement-native", "2.0"), [boxes], ["completed"]
            )
        ),
        ocr=SimpleNamespace(
            recognize=lambda *_: TextExtractionResult(
                EngineInfo("replacement-ocr", "3.0"),
                [[TextBox(1, box.text, box.bbox, confidence=0.9) for box in boxes]],
                ["completed"],
            )
        ),
        semantic_parser=SimpleNamespace(parse=lambda _, pages: pipeline._group_blocks(pages)),
    )

    output = tmp_path / "result"
    pipeline.convert_pdf(
        source,
        output,
        ConversionOptions(created_at="2026-08-12T00:00:00Z"),
        tools=tools,
    )
    records = [json.loads(line) for line in (output / "provenance/elements.jsonl").read_text().splitlines()]
    engines = {
        (candidate["method"], candidate["engine"], candidate["engine_version"])
        for record in records
        for source_record in record["sources"]
        for candidate in source_record["candidates"]
    }
    assert ("native-pdf", "replacement-native", "2.0") in engines
    assert ("ocr", "replacement-ocr", "3.0") in engines


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
    source.write_bytes(b"%PDF-1.4\n")
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
    source.write_bytes(b"%PDF-1.4\n")
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
    source.write_bytes(b"%PDF-1.4\n")
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
    source.write_bytes(b"%PDF-1.4\n")
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
    source.write_bytes(b"%PDF-1.4\n")
    output = source if kind == "same" else case
    with pytest.raises(ConversionError, match="input path"):
        pipeline.convert_pdf(source, output, ConversionOptions(replace=True))
    assert source.read_bytes() == b"%PDF-1.4\n"


def test_output_symlink_and_symlinked_parent_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\n")
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


def test_url_descriptor_uses_verified_cache_and_records_origin(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_engines(monkeypatch)
    pdf_bytes = b"%PDF-1.4\nremote-fixture\n"
    descriptor = tmp_path / "source.json"
    value = _write_descriptor(descriptor, pdf_bytes)
    download_cache = tmp_path / "downloads"
    download_cache.mkdir()
    (download_cache / f"{value['sha256']}.pdf").write_bytes(pdf_bytes)
    output = tmp_path / "result"
    pipeline.convert_pdf(
        descriptor,
        output,
        ConversionOptions(
            created_at="2026-08-12T00:00:00Z",
            download_cache_dir=download_cache,
        ),
    )
    source = json.loads((output / "manifest.json").read_text())["sources"][0]
    assert source["original_name"] == "paper-v1.pdf"
    assert source["sha256"] == value["sha256"]
    assert source["x-origin"] == {
        "kind": "remote-url",
        "url": value["url"],
        "final_url": value["url"],
        "sha256": value["sha256"],
    }


def test_url_descriptor_requires_network_on_cache_miss(tmp_path: Path) -> None:
    descriptor = tmp_path / "source.json"
    _write_descriptor(descriptor, b"%PDF-1.4\nmissing\n")
    output = tmp_path / "result"
    with pytest.raises(ConversionError, match="--allow-network"):
        pipeline.convert_pdf(
            descriptor,
            output,
            ConversionOptions(download_cache_dir=tmp_path / "downloads"),
        )
    assert not output.exists()


@pytest.mark.parametrize("payload", [b"not-pdf!!", b"%PDF-wrong"])
def test_failed_remote_verification_leaves_no_cache_or_output(
    tmp_path: Path, monkeypatch: Any, payload: bytes
) -> None:
    expected = b"%PDF-right"
    descriptor_path = tmp_path / "source.json"
    descriptor = _write_descriptor(descriptor_path, expected)
    cache = tmp_path / "downloads"

    monkeypatch.setattr(
        pipeline,
        "_open_remote_response",
        lambda *_: (_RemoteConnection(), _RemoteResponse(payload)),
    )
    with pytest.raises(ConversionError, match="not a PDF|SHA-256 mismatch|size mismatch"):
        pipeline.convert_pdf(
            descriptor_path,
            tmp_path / "result",
            ConversionOptions(allow_network=True, download_cache_dir=cache),
        )
    assert not (tmp_path / "result").exists()
    assert not (cache / f"{descriptor['sha256']}.pdf").exists()
    assert not list(cache.glob(".download-*"))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/paper.pdf",
        "http://sub.localhost/paper.pdf",
        "http://127.0.0.1/paper.pdf",
        "http://10.0.0.1/paper.pdf",
        "http://169.254.1.1/paper.pdf",
        "http://[::1]/paper.pdf",
        "http://[fc00::1]/paper.pdf",
        "http://[ff02::1]/paper.pdf",
        "http://0.0.0.0/paper.pdf",
        "http://192.0.2.1/paper.pdf",
    ],
)
def test_remote_url_rejects_local_or_non_global_literal(url: str) -> None:
    with pytest.raises(ConversionError, match="localhost|non-global"):
        pipeline._validate_remote_url(url)


def test_dns_rejects_host_if_any_candidate_is_private(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        pipeline.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ],
    )
    with pytest.raises(ConversionError, match="non-global"):
        pipeline._resolve_global_addresses("example.test", 443, time.monotonic() + 1)


def test_connection_rejects_rebound_private_peer(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        pipeline,
        "_resolve_global_addresses",
        lambda *_: ["93.184.216.34"],
    )

    class ReboundSocket:
        def getpeername(self) -> tuple[str, int]:
            return "127.0.0.1", 80

        def settimeout(self, _: float) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(pipeline.socket, "create_connection", lambda *_args, **_kwargs: ReboundSocket())
    with pytest.raises(ConversionError, match="non-global"):
        pipeline._open_remote_response("http://example.test/paper.pdf", time.monotonic() + 1)


def test_redirect_to_private_address_is_rejected(tmp_path: Path, monkeypatch: Any) -> None:
    source = b"%PDF-right"
    descriptor_path = tmp_path / "source.json"
    descriptor = _write_descriptor(descriptor_path, source)
    calls = 0

    def redirect(*_: object) -> tuple[Any, Any]:
        nonlocal calls
        calls += 1
        return _RemoteConnection(), _RemoteResponse(status=302, location="http://127.0.0.1/private")

    monkeypatch.setattr(pipeline, "_open_remote_response", redirect)
    with pytest.raises(ConversionError, match="non-global"):
        pipeline._download_pdf(descriptor, tmp_path / "cache", True)
    assert calls == 1


def test_redirect_limit_is_enforced(tmp_path: Path, monkeypatch: Any) -> None:
    descriptor_path = tmp_path / "source.json"
    descriptor = _write_descriptor(descriptor_path, b"%PDF-right")
    calls = 0

    def redirect(*_: object) -> tuple[Any, Any]:
        nonlocal calls
        calls += 1
        return _RemoteConnection(), _RemoteResponse(
            status=302, location=f"https://example.test/redirect-{calls}"
        )

    monkeypatch.setattr(pipeline, "_open_remote_response", redirect)
    with pytest.raises(ConversionError, match="exceeded 5 redirects"):
        pipeline._download_pdf(descriptor, tmp_path / "cache", True)
    assert calls == pipeline.MAX_REMOTE_REDIRECTS + 1


def test_overall_deadline_applies_during_streaming(tmp_path: Path, monkeypatch: Any) -> None:
    descriptor_path = tmp_path / "source.json"
    descriptor = _write_descriptor(descriptor_path, b"%PDF-right")
    monkeypatch.setattr(pipeline, "REMOTE_TOTAL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        pipeline,
        "_open_remote_response",
        lambda *_: (_RemoteConnection(), _RemoteResponse(b"%PDF", read_delay=0.02)),
    )
    with pytest.raises(ConversionError, match="overall download deadline"):
        pipeline._download_pdf(descriptor, tmp_path / "cache", True)


def test_dns_resolution_obeys_overall_deadline(monkeypatch: Any) -> None:
    monkeypatch.setattr(pipeline, "REMOTE_TIMEOUT_SECONDS", 0.01)

    def slow_dns(*_: object, **__: object) -> list[object]:
        time.sleep(0.05)
        return []

    monkeypatch.setattr(pipeline.socket, "getaddrinfo", slow_dns)
    with pytest.raises(ConversionError, match="DNS resolution"):
        pipeline._resolve_global_addresses("example.test", 443, time.monotonic() + 0.01)


def test_corrupt_cache_is_atomically_recovered_once_under_concurrency(
    tmp_path: Path, monkeypatch: Any
) -> None:
    payload = b"%PDF-right"
    descriptor_path = tmp_path / "source.json"
    descriptor = _write_descriptor(descriptor_path, payload)
    cache = tmp_path / "cache"
    cache.mkdir()
    target = cache / f"{descriptor['sha256']}.pdf"
    target.write_bytes(b"corrupt")
    calls = 0
    calls_lock = threading.Lock()

    def download(*_: object) -> tuple[Any, Any]:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return _RemoteConnection(), _RemoteResponse(payload)

    monkeypatch.setattr(pipeline, "_open_remote_response", download)
    results: list[Path] = []

    def worker() -> None:
        results.append(pipeline._download_pdf(descriptor, cache, True)[0])

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == 1
    assert results == [target, target]
    assert target.read_bytes() == payload


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(url="file:///tmp/paper.pdf"),
        lambda value: value.update(url="https://user:secret@example.test/paper.pdf"),
        lambda value: value.update(sha256="A" * 64),
        lambda value: value.update(size=0),
        lambda value: value.update(original_name="../paper.pdf"),
        lambda value: value.update(extra=True),
    ],
)
def test_url_descriptor_rejects_ambiguous_or_unsafe_values(tmp_path: Path, mutation: Any) -> None:
    descriptor = tmp_path / "source.json"
    value = _write_descriptor(descriptor, b"%PDF-1.4\n")
    mutation(value)
    descriptor.write_text(json.dumps(value) + "\n")
    with pytest.raises(ConversionError):
        pipeline.convert_pdf(descriptor, tmp_path / "result")


def test_golden_projection_detects_structural_regression(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_engines(monkeypatch)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\ngolden\n")
    output = tmp_path / "result"
    pipeline.convert_pdf(
        source,
        output,
        ConversionOptions(created_at="2026-08-12T00:00:00Z"),
    )
    expected = build_projection(output)
    assert compare_projection(expected, build_projection(output)) == []
    document = output / "content/document.xml"
    document.write_text(document.read_text().replace("Minimal Real Title", "Regressed Title"))
    assert "document_sha256" in compare_projection(expected, build_projection(output))


def test_golden_update_requires_exact_confirmation(tmp_path: Path) -> None:
    with pytest.raises(GoldenError, match="--confirm-update"):
        update_golden(tmp_path / "package", tmp_path / "golden", "case-001", "yes")
    assert not (tmp_path / "golden").exists()


def _golden_update_fixture(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path, dict[str, Any]]:
    _fake_engines(monkeypatch)
    payload = b"%PDF-1.4\ngolden-update\n"
    golden = tmp_path / "paper-v1"
    golden.mkdir()
    descriptor = _write_descriptor(golden / "source.json", payload)
    (golden / "README.md").write_text("keep\n")
    (golden / "expected").mkdir()
    (golden / "expected/old.txt").write_text("old\n")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{descriptor['sha256']}.pdf").write_bytes(payload)
    package = tmp_path / "package"
    pipeline.convert_pdf(
        golden / "source.json",
        package,
        ConversionOptions(
            created_at="2026-08-12T00:00:00Z",
            download_cache_dir=cache,
        ),
    )
    return package, golden, descriptor


def test_golden_update_rejects_wrong_target_and_package_identity(tmp_path: Path, monkeypatch: Any) -> None:
    package, golden, _ = _golden_update_fixture(tmp_path, monkeypatch)
    with pytest.raises(GoldenError, match="directory name"):
        update_golden(package, golden, "different-case", "different-case")
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0]["size"] += 1
    manifest_path.write_text(json.dumps(manifest) + "\n")
    with pytest.raises(GoldenError, match="source identity"):
        update_golden(package, golden, "paper-v1", "paper-v1")
    assert (golden / "expected/old.txt").read_text() == "old\n"


def test_golden_update_copy_failure_preserves_old_tree(tmp_path: Path, monkeypatch: Any) -> None:
    package, golden, _ = _golden_update_fixture(tmp_path, monkeypatch)
    real_copyfile = golden_module.shutil.copyfile

    def fail_package_copy(source: Any, target: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(source).resolve() == (package / "manifest.json").resolve():
            raise OSError("simulated copy failure")
        return real_copyfile(source, target, *args, **kwargs)

    monkeypatch.setattr(golden_module.shutil, "copyfile", fail_package_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        update_golden(package, golden, "paper-v1", "paper-v1")
    assert (golden / "expected/old.txt").read_text() == "old\n"
    assert (golden / "README.md").read_text() == "keep\n"


def test_golden_update_publish_failure_rolls_back(tmp_path: Path, monkeypatch: Any) -> None:
    package, golden, _ = _golden_update_fixture(tmp_path, monkeypatch)
    real_replace = golden_module.os.replace
    calls = 0

    def fail_publish(source: Any, target: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publish failure")
        real_replace(source, target)

    monkeypatch.setattr(golden_module.os, "replace", fail_publish)
    with pytest.raises(OSError, match="simulated publish failure"):
        update_golden(package, golden, "paper-v1", "paper-v1")
    assert (golden / "expected/old.txt").read_text() == "old\n"
    assert (golden / "README.md").read_text() == "keep\n"


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
    blocks = pipeline._group_blocks([page]).blocks
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
    structure = pipeline._group_blocks([page])
    assert structure.title.text == real_title.text
    blocks = structure.blocks
    body_texts = [block.text for block in blocks]
    assert real_title.text not in body_texts
    assert stamp.text not in body_texts
    assert body.text in body_texts


def test_front_matter_and_cross_page_abstract_are_promoted(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    Image.new("RGB", (300, 300), "white").save(image)
    first = [
        TextBox(1, "A Reliable Article Title", (0.2, 0.10, 0.8, 0.14), 17),
        TextBox(1, "Ada Lovelace , Alan Turing", (0.25, 0.18, 0.75, 0.20), 12),
        TextBox(1, "1Example Research Institute, London.", (0.2, 0.23, 0.8, 0.25), 11),
        TextBox(1, "Abstract", (0.45, 0.50, 0.55, 0.52), 9),
        TextBox(1, "An abstract that continues to the next", (0.2, 0.55, 0.8, 0.57), 9),
        TextBox(1, "page without becoming body text.", (0.2, 0.70, 0.8, 0.72), 9),
        TextBox(1, "1", (0.49, 0.77, 0.51, 0.79), 10),
    ]
    second = [
        TextBox(2, "The final abstract sentence.", (0.2, 0.09, 0.8, 0.11), 9),
        TextBox(2, "Ordinary article body begins here.", (0.2, 0.20, 0.8, 0.22), 10),
        TextBox(2, "2", (0.49, 0.77, 0.51, 0.79), 10),
    ]
    pages = [
        pipeline.PageData(1, 72, 72, 0, image, 300, 300, first, first),
        pipeline.PageData(2, 72, 72, 0, image, 300, 300, second, second),
    ]
    structure = pipeline._group_blocks(pages)
    assert [author.text for author in structure.front.authors] == ["Ada Lovelace", "Alan Turing"]
    assert len(structure.front.affiliations) == 1
    assert structure.front.abstract is not None
    assert structure.front.abstract.text.endswith("The final abstract sentence.")
    assert " 1 " not in f" {structure.front.abstract.text} "
    assert sum(item["type"] == "page-number" for item in structure.omissions) == 2


def test_detected_table_is_explicitly_omitted(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    Image.new("RGB", (300, 300), "white").save(image)
    boxes = [
        TextBox(1, "Title", (0.2, 0.02, 0.8, 0.05), 20),
        TextBox(1, "Table 1 Experimental values", (0.2, 0.10, 0.8, 0.12), 8),
        TextBox(1, "Parameter Value", (0.2, 0.14, 0.8, 0.16), 8),
        TextBox(1, "loss 27 dB", (0.2, 0.18, 0.8, 0.20), 8),
        TextBox(1, "Body resumes here.", (0.2, 0.25, 0.8, 0.27), 10),
    ]
    page = pipeline.PageData(1, 72, 72, 0, image, 300, 300, boxes, boxes)
    structure = pipeline._group_blocks([page])
    reasons = [item["reason"] for item in structure.omissions]
    assert "Table detected, but reliable cell structure cannot yet be reconstructed." in reasons
    assert all("Table 1" not in block.text for block in structure.blocks)
