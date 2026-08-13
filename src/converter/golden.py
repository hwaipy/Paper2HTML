from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from .pipeline import ConversionError, _descriptor
from .quality import build_quality_report

STRUCTURED_FILES = (
    "manifest.json",
    "content/document.xml",
    "provenance/pages.jsonl",
    "provenance/elements.jsonl",
    "provenance/omissions.jsonl",
    "validation/report.json",
)


class GoldenError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_bytes(payload)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _dhash(image: Image.Image) -> str:
    resized = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(resized.tobytes())
    bits = [
        pixels[row * 9 + column] > pixels[row * 9 + column + 1] for row in range(8) for column in range(8)
    ]
    value = sum(int(bit) << index for index, bit in enumerate(bits))
    return f"{value:016x}"


def _element_projection(record: dict[str, Any]) -> dict[str, Any]:
    sources = []
    for source in record["sources"]:
        candidates = {candidate["method"]: candidate for candidate in source.get("candidates", [])}
        native = candidates.get("native-pdf", {})
        ocr = candidates.get("ocr", {})
        sources.append(
            {
                "source_id": source["source_id"],
                "physical_page": source["physical_page"],
                "logical_page_id": source["logical_page_id"],
                "page_image": source["page_image"],
                "regions": source["regions"],
                "native_text_sha256": _sha256_bytes(str(native.get("text", "")).encode()),
                "ocr_present": "ocr" in candidates,
                "ocr_nonempty": bool(ocr.get("text")),
            }
        )
    return {
        "element_id": record["element_id"],
        "xml_path": record["xml_path"],
        "reading_order": record["reading_order"],
        "sources": sources,
        "decision_method": record["decision"]["method"],
        "revisions": record.get("revisions", []),
        "resources": record.get("resources", []),
    }


def build_projection(package: Path) -> dict[str, Any]:
    package = package.resolve()
    missing = [relative for relative in STRUCTURED_FILES if not (package / relative).is_file()]
    if missing:
        raise GoldenError(f"package lacks required files: {', '.join(missing)}")
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    pages = _jsonl(package / "provenance/pages.jsonl")
    elements = _jsonl(package / "provenance/elements.jsonl")
    omissions = _jsonl(package / "provenance/omissions.jsonl")
    report = json.loads((package / "validation/report.json").read_text(encoding="utf-8"))
    image_summaries = []
    for page in pages:
        image_path = package / page["image"]
        if not image_path.is_file():
            raise GoldenError(f"page image is missing: {page['image']}")
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            image_summaries.append(
                {
                    "path": page["image"],
                    "width": rgb.width,
                    "height": rgb.height,
                    "rgb_sha256": _sha256_bytes(rgb.tobytes()),
                    "dhash": _dhash(rgb),
                }
            )
    element_projection = [_element_projection(record) for record in elements]
    poppler_versions = sorted(
        {
            candidate["engine_version"]
            for record in elements
            for source in record.get("sources", [])
            for candidate in source.get("candidates", [])
            if candidate.get("method") == "native-pdf"
        }
    )
    vision_versions = sorted(
        {
            candidate["engine_version"]
            for record in elements
            for source in record.get("sources", [])
            for candidate in source.get("candidates", [])
            if candidate.get("method") == "ocr"
        }
    )
    stable_report = {
        "valid": report["valid"],
        "checks": report["checks"],
        "statistics": report.get("statistics", {}),
        "error_codes": sorted(error["code"] for error in report["errors"]),
        "warning_codes": sorted(warning["code"] for warning in report["warnings"]),
    }
    quality = build_quality_report(package)
    return {
        "format": "paper2html-golden-projection",
        "format_version": "1",
        "notice": "This is a regression projection, not a complete or conforming P2H Package.",
        "source": manifest["sources"][0],
        "package_id": manifest["package_id"],
        "document_sha256": _sha256_bytes((package / "content/document.xml").read_bytes()),
        "pages_sha256": _canonical_sha(pages),
        "elements_sha256": _canonical_sha(element_projection),
        "omissions_sha256": _canonical_sha(omissions),
        "page_count": len(pages),
        "element_count": len(elements),
        "omission_count": len(omissions),
        "page_images": image_summaries,
        "engine_fingerprint": {"poppler": poppler_versions, "vision": vision_versions},
        "validation": stable_report,
        "content_quality": quality,
    }


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def compare_projection(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    for key in (
        "format",
        "format_version",
        "source",
        "package_id",
        "document_sha256",
        "pages_sha256",
        "elements_sha256",
        "omissions_sha256",
        "page_count",
        "element_count",
        "omission_count",
        "validation",
        "content_quality",
    ):
        if expected.get(key) != actual.get(key):
            differences.append(key)
    expected_images = expected.get("page_images", [])
    actual_images = actual.get("page_images", [])
    if len(expected_images) != len(actual_images):
        differences.append("page_images.count")
    same_poppler = expected.get("engine_fingerprint", {}).get("poppler") == actual.get(
        "engine_fingerprint", {}
    ).get("poppler")
    for index, (wanted, observed) in enumerate(zip(expected_images, actual_images, strict=False), 1):
        for key in ("path", "width", "height"):
            if wanted.get(key) != observed.get(key):
                differences.append(f"page_images[{index}].{key}")
        if same_poppler:
            if wanted.get("rgb_sha256") != observed.get("rgb_sha256"):
                differences.append(f"page_images[{index}].rgb_sha256")
        elif _hamming(str(wanted.get("dhash", "0")), str(observed.get("dhash", "0"))) > 6:
            differences.append(f"page_images[{index}].dhash")
    return differences


def update_golden(package: Path, golden: Path, case_id: str, confirmation: str) -> None:
    if confirmation != case_id:
        raise GoldenError(f"refusing update: --confirm-update must exactly equal {case_id!r}")
    golden = golden.absolute()
    if golden.name != case_id:
        raise GoldenError("refusing update: case ID must exactly equal the golden directory name")
    if golden.is_symlink() or not golden.is_dir():
        raise GoldenError("refusing update: golden target must be an existing non-symlink directory")
    descriptor_path = golden / "source.json"
    try:
        descriptor = _descriptor(descriptor_path)
    except ConversionError as exc:
        raise GoldenError(f"cannot read committed source descriptor: {exc}") from exc
    if descriptor.get("case_id") != case_id:
        raise GoldenError("refusing update: descriptor case_id does not match the requested case")
    projection = build_projection(package)
    source = projection.get("source", {})
    origin = source.get("x-origin", {}) if isinstance(source, dict) else {}
    expected_identity = {
        "id": "src-001",
        "role": "primary",
        "media_type": "application/pdf",
        "original_name": descriptor.get("original_name"),
        "sha256": descriptor.get("sha256"),
        "size": descriptor.get("size"),
    }
    actual_identity = {
        "id": source.get("id") if isinstance(source, dict) else None,
        "role": source.get("role") if isinstance(source, dict) else None,
        "media_type": source.get("media_type") if isinstance(source, dict) else None,
        "original_name": source.get("original_name") if isinstance(source, dict) else None,
        "sha256": source.get("sha256") if isinstance(source, dict) else None,
        "size": source.get("size") if isinstance(source, dict) else None,
    }
    if actual_identity != expected_identity:
        raise GoldenError("refusing update: package source identity differs from source.json")
    if (
        not isinstance(origin, dict)
        or origin.get("kind") != "remote-url"
        or origin.get("url") != descriptor.get("url")
        or origin.get("sha256") != descriptor.get("sha256")
    ):
        raise GoldenError("refusing update: package remote origin differs from source.json")

    temporary = Path(tempfile.mkdtemp(prefix=f".{golden.name}-update-", dir=golden.parent))
    backup: Path | None = None
    try:
        shutil.copytree(golden, temporary, dirs_exist_ok=True)
        expected = temporary / "expected"
        if expected.exists():
            shutil.rmtree(expected)
        for relative in STRUCTURED_FILES:
            package_file = package / relative
            target = expected / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package_file, target)
        (temporary / "projection.json").write_text(
            json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (temporary / "quality.json").write_text(
            json.dumps(projection["content_quality"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        backup = Path(tempfile.mkdtemp(prefix=f".{golden.name}-previous-", dir=golden.parent))
        backup.rmdir()
        os.replace(golden, backup)
        try:
            os.replace(temporary, golden)
        except Exception:
            os.replace(backup, golden)
            backup = None
            raise
        shutil.rmtree(backup)
        backup = None
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare or deliberately update a golden projection")
    parser.add_argument("package", type=Path)
    parser.add_argument("golden", type=Path)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--case-id")
    parser.add_argument("--confirm-update")
    args = parser.parse_args(argv)
    try:
        if args.update:
            if not args.case_id:
                raise GoldenError("--case-id is required with --update")
            update_golden(args.package, args.golden, args.case_id, args.confirm_update or "")
            print(f"updated golden projection: {args.golden}")
            return 0
        expected = json.loads((args.golden / "projection.json").read_text(encoding="utf-8"))
        differences = compare_projection(expected, build_projection(args.package))
        if differences:
            print("golden regression mismatch: " + ", ".join(differences), file=sys.stderr)
            return 1
        print("golden regression matched")
        return 0
    except (GoldenError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"golden check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
