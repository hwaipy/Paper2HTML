from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from lxml import etree
from PIL import Image, UnidentifiedImageError

from src.validator.validator import ADDRESSABLE_TAGS, ValidationOptions, validate_package


class QualityError(RuntimeError):
    pass


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityError(f"cannot read {path}: {exc}") from exc


def _text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _formula_only_text(value: str) -> bool:
    if any(len(word) >= 3 for word in re.findall(r"[A-Za-z]+", value)):
        return False
    short_tokens = sum(len(word) <= 2 for word in re.findall(r"[A-Za-z]+", value))
    math_signals = len(re.findall(r"[0-9=+−<>√Σ∆δφϕηµενπτ|⌊⌋]", value))
    return math_signals > 0 or short_tokens >= 2


def _candidate_consistency(tree: etree._ElementTree, elements: list[dict[str, Any]]) -> list[dict[str, str]]:
    records = {record.get("element_id"): record for record in elements}
    findings: list[dict[str, str]] = []
    for node in tree.xpath("//*[@id]"):
        if etree.QName(node).localname not in ADDRESSABLE_TAGS:
            continue
        element_id = node.get("id")
        record = records.get(element_id)
        if not record:
            findings.append({"code": "provenance_missing", "element_id": str(element_id)})
            continue
        final = _text(" ".join(str(value) for value in node.itertext() if str(value).strip()))
        revisions = record.get("revisions", [])
        compact_final = re.sub(r"[^\w]", "", final.casefold())
        if any(
            re.sub(r"[^\w]", "", _text(str(revision.get("after", ""))).casefold()) == compact_final
            for revision in revisions
        ):
            continue
        by_method: dict[str, list[str]] = defaultdict(list)
        for source in record.get("sources", []):
            for candidate in source.get("candidates", []):
                if candidate.get("text"):
                    by_method[str(candidate.get("method", "unknown"))].append(str(candidate["text"]))
        candidates = [_text(" ".join(parts)) for parts in by_method.values()]
        if not final or etree.QName(node).localname == "fig":
            continue
        comparable = False
        for candidate in candidates:
            a, b = final.casefold(), candidate.casefold()
            ratio = SequenceMatcher(None, a, b).ratio()
            tokens_a = Counter(re.findall(r"[\w]+", a))
            tokens_b = Counter(re.findall(r"[\w]+", b))
            overlap = sum((tokens_a & tokens_b).values()) / max(sum(tokens_a.values()), 1)
            candidate_size = sum(tokens_b.values())
            final_size = sum(tokens_a.values())
            if (
                a in b
                or b in a
                or ratio >= 0.78
                or overlap >= 0.9
                and candidate_size <= max(final_size * 2, final_size + 6)
            ):
                comparable = True
                break
        if not comparable:
            findings.append({"code": "candidate_text_mismatch", "element_id": str(element_id)})
    return findings


def _resource_findings(root: Path, elements: list[dict[str, Any]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for record in elements:
        for resource in record.get("resources", []):
            relative = resource.get("path")
            if not isinstance(relative, str):
                findings.append(
                    {"code": "resource_path_invalid", "element_id": str(record.get("element_id", ""))}
                )
                continue
            path = root / relative
            try:
                with Image.open(path) as image:
                    image.verify()
                    if image.format != "PNG":
                        findings.append({"code": "resource_not_png", "path": relative})
            except (OSError, UnidentifiedImageError):
                findings.append({"code": "resource_unreadable", "path": relative})
    return findings


def build_quality_report(
    package: Path,
    *,
    cache_dir: Path | None = None,
    allow_network: bool = False,
) -> dict[str, Any]:
    root = package.resolve()
    result = validate_package(
        root,
        ValidationOptions(cache_dir=cache_dir, allow_network=allow_network),
    )
    if result.operational_error:
        detail = result.report.get("errors", [{}])[0].get("message", "validator operational error")
        raise QualityError(str(detail))
    try:
        tree = etree.parse(str(root / "content/document.xml"))
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, etree.XMLSyntaxError, json.JSONDecodeError) as exc:
        raise QualityError(f"cannot inspect package: {exc}") from exc
    elements = _jsonl(root / "provenance/elements.jsonl")
    omissions = _jsonl(root / "provenance/omissions.jsonl")
    pages = _jsonl(root / "provenance/pages.jsonl")

    reference_labels = [
        int(value) for value in tree.xpath("//ref/label/text()") if isinstance(value, str) and value.isdigit()
    ]
    body_paragraphs = [_text(element.xpath("string()")) for element in tree.xpath("//body//p")]
    page_number_paragraphs = [value for value in body_paragraphs if re.fullmatch(r"\d{1,4}", value)]
    fragment_paragraphs = [
        value for value in body_paragraphs if len(value) <= 4 and not re.search(r"[A-Za-z]{2,}", value)
    ]
    omission_types = Counter(record.get("type", "unknown") for record in omissions)
    omission_reasons = Counter(record.get("reason", "") for record in omissions)
    metrics = {
        "pages": len(pages),
        "addressable_elements": len(elements),
        "article_titles": len(tree.xpath("//article-title")),
        "authors": len(tree.xpath("//contrib[@contrib-type='author']")),
        "author_affiliation_xrefs": len(tree.xpath("//contrib/xref[@ref-type='aff']")),
        "equal_contribution_notes": len(tree.xpath("//author-notes/fn[@fn-type='equal']")),
        "affiliations": len(tree.xpath("//article-meta/aff")),
        "abstracts": len(tree.xpath("//article-meta/abstract")),
        "publication_dates": len(tree.xpath("//article-meta/pub-date")),
        "body_sections": len(tree.xpath("//body//sec")),
        "body_paragraphs": len(body_paragraphs),
        "references": len(tree.xpath("//ref-list/ref")),
        "figures": len(tree.xpath("//fig")),
        "tables": len(tree.xpath("//table-wrap")),
        "formulas": len(tree.xpath("//disp-formula | //inline-formula")),
        "display_math_regions_omitted_with_evidence": omission_reasons[
            "Detected display-math region; semantic formula recovery remains partial."
        ],
        "tables_omitted_with_evidence": omission_reasons[
            "Table detected, but reliable cell structure cannot yet be reconstructed."
        ],
        "omissions": len(omissions),
        "page_number_omissions": omission_types["page-number"],
        "page_number_paragraphs": len(page_number_paragraphs),
        "short_fragment_paragraphs": len(fragment_paragraphs),
        "formula_only_paragraphs": sum(_formula_only_text(value) for value in body_paragraphs),
    }
    validation_failures = [
        {"code": error.get("code", "unknown"), "message": error.get("message", "")}
        for error in result.report.get("errors", [])
        if error.get("code") != "page_coverage_not_audited"
    ]
    consistency_findings = _candidate_consistency(tree, elements)
    resource_findings = _resource_findings(root, elements)
    criteria: dict[str, dict[str, Any]] = {
        "package_integrity": {
            "status": "failed" if validation_failures else "passed",
            "findings": validation_failures,
        },
        "xml_provenance_consistency": {
            "status": "failed" if consistency_findings else "passed",
            "findings": consistency_findings,
        },
        "resource_integrity": {
            "status": "failed" if resource_findings else "passed",
            "findings": resource_findings,
        },
        "front_matter": {
            "status": "partial" if metrics["article_titles"] == metrics["abstracts"] == 1 else "failed",
            "reason": (
                "Presence and source consistency are checked; semantic correctness still requires review."
            ),
        },
        "semantic_structure": {
            "status": "partial"
            if metrics["body_sections"] >= 1
            and metrics["body_paragraphs"]
            and not page_number_paragraphs
            and metrics["formula_only_paragraphs"] == 0
            else "failed",
            "short_fragment_examples": fragment_paragraphs[:10],
        },
        "references": {
            "status": "partial"
            if reference_labels and reference_labels == list(range(1, len(reference_labels) + 1))
            else "failed",
            "labels_consecutive": reference_labels == list(range(1, len(reference_labels) + 1)),
        },
        "figures_tables_formulas": {
            "status": "partial",
            "reason": "Figures are normalized; unresolved table/formula semantics are explicit omissions.",
        },
        "non_body_classification": {
            "status": "partial" if not page_number_paragraphs else "failed",
            "reason": "Heuristic classification has evidence but is not an independent coverage proof.",
        },
    }
    return {
        "format": "paper2html-content-quality-report",
        "format_version": "2",
        "package_id": manifest.get("package_id"),
        "status": "failed" if any(item["status"] == "failed" for item in criteria.values()) else "partial",
        "metrics": metrics,
        "criteria": criteria,
        "omission_types": dict(sorted(omission_types.items())),
        "omission_reasons": dict(sorted(omission_reasons.items())),
        "known_limitations": [
            "Table structure is not yet reconstructed.",
            "Display mathematics without reliable TeX is represented by a complete-region omission.",
            "Semantic quality criteria remain partial until independently reviewed.",
            "Independent visible-object page coverage is reported by the normative validator.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure semantic quality of a generated P2H package")
    parser.add_argument("package", type=Path)
    parser.add_argument("--cache-dir", type=Path, help="validator resource cache")
    parser.add_argument(
        "--allow-network", action="store_true", help="fetch missing locked validator resources"
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        report = build_quality_report(
            args.package,
            cache_dir=args.cache_dir,
            allow_network=args.allow_network,
        )
    except QualityError as exc:
        print(f"quality check failed: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"content quality: {report['status']}")
        for name, criterion in report["criteria"].items():
            print(f"  {name:28} {criterion['status']}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
