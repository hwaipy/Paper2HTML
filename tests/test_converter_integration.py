from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
def test_real_arxiv_case_is_byte_reproducible(tmp_path: Path) -> None:
    if os.environ.get("P2H_RUN_INTEGRATION") != "1":
        pytest.skip("set P2H_RUN_INTEGRATION=1 to run the real 17-page conversion")
    source = Path("testdata/cases/papers/arxiv-2503-17744v1/input/2503.17744v1.pdf")
    if not source.is_file():
        pytest.skip("ignored persistent arXiv test case is not present")
    outputs = [tmp_path / "first", tmp_path / "second"]
    for output in outputs:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.converter.cli",
                str(source),
                str(output),
                "--created-at",
                "2026-08-12T00:00:00Z",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    def files(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()
        }

    assert files(outputs[0]) == files(outputs[1])
    xml = (outputs[0] / "content/document.xml").read_text()
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
