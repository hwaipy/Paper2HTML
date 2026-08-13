from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from lxml import etree

from src.converter.golden import build_projection, compare_projection


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
        "assets/content/figures/fig-000001.png",
        "assets/content/figures/fig-000002.png",
        "assets/content/figures/fig-000003.png",
    ):
        assert (output / relative).read_bytes() == (local_output / relative).read_bytes()

    xml = (output / "content/document.xml").read_text()
    tree = etree.parse(str(output / "content/document.xml"))
    expected_title = "Free-Space Twin-Field Quantum Key Distribution"
    assert f'<article-title id="title-000002">{expected_title}</article-title>' in xml
    assert xml.count(expected_title) == 1
    assert '<article-title id="title-000002">arXiv:' not in xml
    assert len(tree.xpath("//contrib[@contrib-type='author']")) == 25
    assert len(tree.xpath("//article-meta/aff")) == 6
    assert len(tree.xpath("//article-meta/abstract")) == 1
    assert len(tree.xpath("//fig[caption and graphic]")) == 3
    assert len(tree.xpath("//contrib/xref[@ref-type='aff']")) == 64
    expected_links = {
        "Yu-Huai Li": ["aff-000001", "aff-000002", "aff-000003"],
        "Cong Jiang": ["aff-000004"],
        "Zeng-Sen Lin": ["aff-000006"],
        "Cheng-Lin Li": ["aff-000001", "aff-000002", "aff-000003"],
        "Fei Zhou": ["aff-000003", "aff-000006"],
        "Hao Li": ["aff-000005"],
    }
    for author, rids in expected_links.items():
        given, surname = author.rsplit(" ", 1)
        contrib = cast(
            list[etree._Element],
            tree.xpath(f"//contrib[name/given-names={given!r} and name/surname={surname!r}]"),
        )[0]
        assert contrib.xpath("./xref[@ref-type='aff']/@rid") == rids
    assert tree.xpath("normalize-space(string(//author-notes/fn[@fn-type='equal']))") == (
        "†These authors contributed equally to this work."
    )
    assert len(tree.xpath("//contrib/xref[@ref-type='author-notes']")) == 3
    assert [int(value) for value in tree.xpath("//ref/label/text()")] == list(range(1, 56))
    assert xml.index("<label>Fig. 1</label>") < xml.index("(PLOB) bound") < xml.index("Furthermore")
    for corruption in (
        ">events</p>",
        ">the free-space channel</p>",
        ">∆L(τ)</p>",
        "of kilometers. the frequency broadening",
        "turbulenceinduced",
        "openchannel",
        "freespace",
        "ratedistance",
        "finitekey",
        "Satelliteto-ground",
        "∆φat",
        "∆νis",
        "1,400 nsof",
        "withQBER",
    ):
        assert corruption not in xml
    assert "turbulence-induced" in xml
    assert "open-channel" in xml
    before_formula = cast(list[etree._Element], tree.xpath("//p[contains(., 'approximatively')]"))[0]
    after_formula = cast(list[etree._Element], tree.xpath("//p[starts-with(., 'where ∆')]"))[0]
    assert str(before_formula.xpath("string()")).endswith("approximatively regarded as")
    assert str(after_formula.xpath("string()")).startswith("where ∆ L(τ)")
    assert "7.1 km atmospheric channel" in str(tree.xpath("normalize-space(string(//fig[2]/caption))"))
    assert "Satellite-to-ground" in str(tree.xpath("normalize-space(string(//ref[9]))"))
    assert "rate-distance" in str(tree.xpath("normalize-space(string(//ref[18]))"))
    assert "proof-of-principle" in str(tree.xpath("normalize-space(string(//ref[20]))"))
    assert "finite-key" in str(tree.xpath("normalize-space(string(//ref[47]))"))
    assert "repeaterless quantum communications" in str(tree.xpath("normalize-space(string(//ref[19]))"))
    assert "Entangling independent photons" in str(tree.xpath("normalize-space(string(//ref[50]))"))
    assert "repeater-less" not in xml
    assert "Entan-gling" not in xml

    omissions = [
        json.loads(line) for line in (output / "provenance/omissions.jsonl").read_text().splitlines()
    ]
    formula = [item for item in omissions if item["reason"].startswith("Detected display-math")]
    assert {item["physical_page"] for item in formula} >= {4, 10}
    assert not any(item["physical_page"] == 8 for item in formula)
    assert any(item["physical_page"] == 4 and item["bbox"][1] < 0.28 < item["bbox"][3] for item in formula)
    assert any(item["physical_page"] == 10 and item["bbox"][1] < 0.43 < item["bbox"][3] for item in formula)
    assert any(
        item["physical_page"] == 11
        and item["reason"].startswith("Table detected")
        and item["bbox"][1] < 0.1 < item["bbox"][3]
        for item in omissions
    )

    elements = {
        record["element_id"]: record
        for record in (
            json.loads(line) for line in (output / "provenance/elements.jsonl").read_text().splitlines()
        )
    }
    author_record = elements["contrib-000001"]
    line_regions = [item["bbox"] for item in author_record["sources"][0]["regions"]]
    assert any(region[2] - region[0] > 0.4 for region in line_regions)
    revision = author_record["revisions"][-1]
    assert revision["x-segmentation-method"] == "character-proportional-layout-interpolation"
    derived = revision["x-derived-bbox"]
    assert derived[2] - derived[0] < 0.16
    ref53 = "https://github.com/hwaipy/InteractionFreePy"
    ref54 = (
        "Hu, X.-L., Jiang, C., Yu, Z.-W., Wang, X.-B.: Universal approach to "
        "sending-or-not-sending twin field quantum key distribution. Quantum Science "
        "and Technology 7(4), 045031 (2022)"
    )
    ref55 = (
        "Vitanov, A., Dupuis, F., Tomamichel, M., Renner, R.: Chain rules for smooth "
        "min- and max-entropies. IEEE Transactions on Information Theory 59(5), 2603–2612 (2013)"
    )
    assert xml.index("224011 (2012)") < xml.index(ref53) < xml.index(ref54) < xml.index(ref55)
