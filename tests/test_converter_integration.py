from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from converter.golden import build_projection, compare_projection


@pytest.mark.integration
def test_real_arxiv_url_matches_committed_golden_projection(tmp_path: Path) -> None:
    if os.environ.get("P2H_RUN_NETWORK_GOLDEN") != "1":
        pytest.skip("set P2H_RUN_NETWORK_GOLDEN=1 to run the real network golden regression")
    golden = Path("tests/golden/arxiv-2503-17744v1")
    source = golden / "source.json"
    output = tmp_path / "remote"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.converter.cli",
            str(source),
            str(output),
            "--created-at",
            "2026-08-12T00:00:00Z",
            "--download-cache-dir",
            str(tmp_path / "downloads"),
            "--secure-dns",
            "--allow-network",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    expected = json.loads((golden / "projection.json").read_text())
    assert compare_projection(expected, build_projection(output)) == []
    validation = subprocess.run(
        [sys.executable, "-m", "src.validator.cli", str(output), "--json"],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert validation.returncode == 1, validation.stdout + validation.stderr
    independent_report = json.loads(validation.stdout)
    assert independent_report["checks"] == expected["validation"]["checks"]
    assert [error["code"] for error in independent_report["errors"]] == expected["validation"]["error_codes"]

    descriptor = json.loads(source.read_text())
    local_source = tmp_path / "downloads" / f"{descriptor['sha256']}.pdf"
    assert local_source.is_file()
    local_output = tmp_path / "local"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.converter.cli",
            str(local_source),
            str(local_output),
            "--created-at",
            "2026-08-12T00:00:00Z",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for relative in (
        "content/document.xml",
        "provenance/pages.jsonl",
        "provenance/elements.jsonl",
        "provenance/omissions.jsonl",
    ):
        assert (output / relative).read_bytes() == (local_output / relative).read_bytes()

    xml = (output / "content/document.xml").read_text()
    expected_title = "Free-Space Twin-Field Quantum Key Distribution"
    assert f'<article-title id="title-000002">{expected_title}</article-title>' in xml
    assert xml.count(expected_title) == 1
    assert '<article-title id="title-000002">arXiv:' not in xml
    ref53 = "[53] https://github.com/hwaipy/InteractionFreePy"
    ref54 = (
        "[54] Hu, X.-L., Jiang, C., Yu, Z.-W., Wang, X.-B.: Universal approach to "
        "sending-or-not-sending twin field quantum key distribution. Quantum Science "
        "and Technology 7(4), 045031 (2022)"
    )
    ref55 = (
        "[55] Vitanov, A., Dupuis, F., Tomamichel, M., Renner, R.: Chain rules for smooth "
        "min- and max-entropies. IEEE Transactions on Information Theory 59(5), 2603–2612 (2013)"
    )
    assert xml.index("224011 (2012)") < xml.index(ref53) < xml.index(ref54) < xml.index(ref55)
