from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from lxml import etree

from validator.resources import ResourceManager
from validator.validator import (
    ValidationOptions,
    _xml_parser,
    validate_package,
    write_report,
)


@pytest.fixture(scope="session")
def cache_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("schema-cache")


def options(cache_dir: Path, evidence: Path | None) -> ValidationOptions:
    return ValidationOptions(cache_dir=cache_dir, allow_network=True, coverage_evidence=evidence)


def test_minimal_package_executes_all_normative_engines(package: tuple[Path, Path], cache_dir: Path) -> None:
    root, evidence = package
    result = validate_package(root, options(cache_dir, evidence))
    assert result.operational_error is False
    assert result.report["valid"] is True, result.report["errors"]
    assert set(result.report["checks"].values()) == {"passed"}
    assert result.exit_code == 0


def test_without_independent_coverage_is_partial(package: tuple[Path, Path], cache_dir: Path) -> None:
    root, _ = package
    result = validate_package(root, options(cache_dir, None))
    assert result.report["valid"] is False
    assert result.report["checks"]["page_coverage"] == "partial"
    assert "page_coverage_not_audited" in {e["code"] for e in result.report["errors"]}
    assert result.exit_code == 1


def test_both_locked_upstream_xsds_compile(cache_dir: Path) -> None:
    manager = ResourceManager(Path("schema/0.1").resolve(), cache_dir, True)
    xml_xsd = manager.fetch(manager._locked("w3c-xml-namespace-xsd"))
    for profile in ("jats-1.3", "bits-2.1"):
        entrypoint = manager.xsd_entrypoint(profile)
        etree.XMLSchema(etree.parse(str(entrypoint), _xml_parser(xml_xsd)))


def test_missing_offline_schema_is_operational_failure(package: tuple[Path, Path], tmp_path: Path) -> None:
    root, evidence = package
    result = validate_package(
        root,
        ValidationOptions(cache_dir=tmp_path / "empty-cache", coverage_evidence=evidence),
    )
    assert result.operational_error is True
    assert result.exit_code == 2
    assert "jats_bits_engine_error" in {error["code"] for error in result.report["errors"]}


def test_missing_package_root_is_operational_and_never_written(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = validate_package(missing, ValidationOptions(cache_dir=tmp_path / "cache"))
    assert result.operational_error is True
    assert result.exit_code == 2
    assert not missing.exists()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.validator.cli",
            str(missing),
            "--cache-dir",
            str(tmp_path / "cli-cache"),
            "--write-report",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert not missing.exists()


def add_formula(root: Path) -> None:
    document = root / "content/document.xml"
    document.write_text(
        document.read_text().replace(
            "Hello world.",
            'Hello <inline-formula id="ineq-000001"><tex-math>x+1</tex-math></inline-formula>.',
        )
    )


@pytest.mark.parametrize("command", [None, "definitely-not-a-p2h-formula-engine"])
def test_missing_formula_engine_is_operational_failure(
    package: tuple[Path, Path], cache_dir: Path, command: str | None
) -> None:
    root, evidence = package
    add_formula(root)
    result = validate_package(
        root,
        ValidationOptions(
            cache_dir=cache_dir,
            allow_network=True,
            coverage_evidence=evidence,
            katex_command=command,
        ),
    )
    assert result.operational_error is True
    assert result.exit_code == 2
    assert "formula_parser_unavailable" in {error["code"] for error in result.report["errors"]}


def test_formula_parser_rejection_is_package_invalid(package: tuple[Path, Path], cache_dir: Path) -> None:
    root, evidence = package
    add_formula(root)
    result = validate_package(
        root,
        ValidationOptions(
            cache_dir=cache_dir,
            allow_network=True,
            coverage_evidence=evidence,
            katex_command=f'{sys.executable} -c "import sys; sys.exit(1)"',
        ),
    )
    assert result.operational_error is False
    assert result.exit_code == 1
    assert "formula_parse_error" in {error["code"] for error in result.report["errors"]}


def test_formula_parser_timeout_is_operational_failure(
    package: tuple[Path, Path], cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, evidence = package
    add_formula(root)

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("formula-check", 10)

    monkeypatch.setattr("validator.validator.subprocess.run", timeout)
    result = validate_package(
        root,
        ValidationOptions(
            cache_dir=cache_dir,
            allow_network=True,
            coverage_evidence=evidence,
            katex_command=sys.executable,
        ),
    )
    assert result.operational_error is True
    assert result.exit_code == 2
    assert "formula_parser_engine_error" in {error["code"] for error in result.report["errors"]}


def test_symlink_is_rejected(package: tuple[Path, Path], cache_dir: Path) -> None:
    root, evidence = package
    (root / "assets/content").mkdir(parents=True)
    (root / "assets/content/alias.png").symlink_to(root / "assets/evidence/pages/src-001/page-000001.png")
    result = validate_package(root, options(cache_dir, evidence))
    assert "symbolic_link" in {error["code"] for error in result.report["errors"]}


@pytest.mark.parametrize(
    ("relative", "mutation", "code"),
    [
        ("manifest.json", lambda p: p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes()), "text_bom"),
        (
            "provenance/pages.jsonl",
            lambda p: p.write_text(p.read_text().replace('"physical_page": 1', '"physical_page": 2')),
            "page_sequence",
        ),
        (
            "assets/evidence/pages/src-001/page-000001.png",
            lambda p: p.write_bytes(b"not png"),
            "image_decode",
        ),
        (
            "content/document.xml",
            lambda p: p.write_text(p.read_text().replace("rid=", "rid=") + "<"),
            "xml_syntax",
        ),
    ],
)
def test_key_negative_cases(
    package: tuple[Path, Path], cache_dir: Path, relative: str, mutation: object, code: str
) -> None:
    root, evidence = package
    mutation(root / relative)  # type: ignore[operator]
    result = validate_package(root, options(cache_dir, evidence))
    assert result.report["valid"] is False
    assert code in {e["code"] for e in result.report["errors"]}


def test_provenance_candidate_and_bbox_errors(package: tuple[Path, Path], cache_dir: Path) -> None:
    root, evidence = package
    path = root / "provenance/elements.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0]["sources"][0]["candidates"] = []
    records[0]["sources"][0]["regions"][0]["bbox"] = [0.9, 0.1, 0.2, 0.3]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    result = validate_package(root, options(cache_dir, evidence))
    codes = {e["code"] for e in result.report["errors"]}
    assert {"ocr_candidate_missing", "native_candidate_missing", "bbox_order"} <= codes


def test_write_report_repairs_self_checksum(package: tuple[Path, Path], cache_dir: Path) -> None:
    root, evidence = package
    result = validate_package(root, options(cache_dir, evidence), writing_report=True)
    write_report(root, result)
    second = validate_package(root, options(cache_dir, evidence))
    assert second.report["valid"] is True, second.report["errors"]
    report_hash = __import__("hashlib").sha256((root / "validation/report.json").read_bytes()).hexdigest()
    checksum_line = next(
        line
        for line in (root / "checksums.sha256").read_text().splitlines()
        if line.endswith("  validation/report.json")
    )
    assert checksum_line.startswith(report_hash)


def test_cli_exit_codes_and_json(package: tuple[Path, Path], cache_dir: Path) -> None:
    root, evidence = package
    command = [
        sys.executable,
        "-m",
        "src.validator.cli",
        str(root),
        "--cache-dir",
        str(cache_dir),
        "--allow-network",
        "--coverage-evidence",
        str(evidence),
        "--json",
    ]
    valid = subprocess.run(command, text=True, capture_output=True, check=False)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    json.loads(valid.stdout)
    (root / "manifest.json").write_text("{}\n")
    invalid = subprocess.run(command, text=True, capture_output=True, check=False)
    assert invalid.returncode == 1


def test_local_trial_package_diagnostics(cache_dir: Path) -> None:
    trial = Path("testdata/runs/arxiv-2503-17744v1-p2h-v0.1-trial")
    if not trial.is_dir():
        pytest.skip("ignored local trial package is not present")
    result = validate_package(trial, ValidationOptions(cache_dir=cache_dir, allow_network=True))
    assert result.report["valid"] is False
    codes = {e["code"] for e in result.report["errors"]}
    assert "page_coverage_not_audited" in codes
