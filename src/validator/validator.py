from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from lxml import etree
from PIL import Image, UnidentifiedImageError
from referencing import Registry, Resource
from saxonche import PySaxonProcessor

from .io import inspect_text, load_json, load_jsonl
from .models import CHECKS, Finding, State
from .resources import ResourceError, ResourceManager, sha256_file

SCHEMA_URL = "https://hwaipy.github.io/Paper2HTML/schema/0.1/validation-report.schema.json"
XLINK = "{http://www.w3.org/1999/xlink}href"
TEXT_TAGS = {
    "article-title",
    "subtitle",
    "title",
    "book-title",
    "book-subtitle",
    "journal-title",
    "journal-subtitle",
    "article-id",
    "book-id",
    "journal-id",
    "isbn",
    "issn",
    "contrib",
    "name",
    "collab",
    "aff",
    "pub-date",
    "abstract",
    "kwd",
    "funding-source",
    "license-p",
    "copyright-statement",
    "sec",
    "p",
    "list",
    "list-item",
    "disp-formula",
    "inline-formula",
    "caption",
    "td",
    "th",
    "fn",
    "ref",
    "boxed-text",
    "preformat",
    "supplementary-material",
}
ADDRESSABLE_TAGS = TEXT_TAGS | {"book-part", "fig", "table-wrap"}
INLINE_ANNOTATION_TAGS = {"bold", "italic", "monospace", "sup", "sub", "underline", "ext-link", "xref"}
_ACTIVE_SCHEMA_DIR: ContextVar[Path] = ContextVar("p2h_schema_dir", default=Path())


@dataclass(frozen=True)
class ValidationOptions:
    schema_dir: Path | None = None
    cache_dir: Path | None = None
    allow_network: bool = False
    coverage_evidence: Path | None = None
    katex_command: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    report: dict[str, Any]
    operational_error: bool = False

    @property
    def exit_code(self) -> int:
        return 2 if self.operational_error else (0 if self.report["valid"] else 1)


class LockedResolver(etree.Resolver):
    def __init__(self, xml_xsd: Path) -> None:
        self.xml_xsd = xml_xsd

    def resolve(self, url: str, public_id: str, context: Any) -> Any:
        if url in {"http://www.w3.org/2001/xml.xsd", "https://www.w3.org/2001/xml.xsd"}:
            return self.resolve_filename(str(self.xml_xsd), context)
        return None


def _default_schema_dir() -> Path:
    candidate = Path(__file__).resolve().parents[2] / "schema" / "0.1"
    if candidate.is_dir():
        return candidate
    raise RuntimeError("Cannot locate schema/0.1; pass --schema-dir")


def _default_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    return Path(root) / "paper2html-validator" if root else Path.home() / ".cache" / "paper2html-validator"


def _schema_validate(instance: Any, name: str, schema_dir: Path) -> list[str]:
    schema_path = schema_dir / name
    schema = json.loads(schema_path.read_text())
    registry = Registry()
    for local_path in schema_dir.glob("*.schema.json"):
        local_schema = json.loads(local_path.read_text())
        resource = Resource.from_contents(local_schema)
        registry = registry.with_resource(local_path.resolve().as_uri(), resource)
        if isinstance(local_schema.get("$id"), str):
            registry = registry.with_resource(local_schema["$id"], resource)
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    return [
        f"{'.'.join(str(x) for x in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    ]


def _validate_schema(
    instance: Any, schema_name: str, path: str, line: int | None, state: State, check: str
) -> bool:
    try:
        errors = _schema_validate(instance, schema_name, _ACTIVE_SCHEMA_DIR.get())
    except Exception as exc:
        state.operational_error = True
        state.error(check, "schema_engine_error", f"Cannot execute {schema_name}: {exc}", path=path)
        return False
    for message in errors:
        state.error(check, "schema_violation", message, path=path, **({"line": line} if line else {}))
    return not errors


def _scan_filesystem(root: Path, state: State) -> set[str]:
    files: set[str] = set()
    folded: dict[str, str] = {}
    if not root.is_dir():
        state.operational_error = True
        state.error("manifest_schema", "package_not_directory", "Package root is not a directory.")
        return files
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        for name in [*dirs, *names]:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                state.error(
                    "manifest_schema", "symbolic_link", "Symbolic links are forbidden.", path=relative
                )
            if name != unicodedata.normalize("NFC", name):
                state.error(
                    "manifest_schema", "path_not_nfc", "Path component is not NFC-normalized.", path=relative
                )
            if not re.fullmatch(r"[a-z0-9._-]+", name):
                state.error(
                    "manifest_schema",
                    "illegal_path_name",
                    "Internal path contains forbidden characters.",
                    path=relative,
                )
            key = relative.casefold()
            if key in folded and folded[key] != relative:
                state.error(
                    "manifest_schema",
                    "casefold_collision",
                    f"Path collides with {folded[key]!r} after case folding.",
                    path=relative,
                )
            folded[key] = relative
        for name in names:
            path = base / name
            if path.is_file() and not path.is_symlink():
                files.add(path.relative_to(root).as_posix())
            elif not path.is_symlink():
                state.error(
                    "manifest_schema",
                    "non_regular_file",
                    "Package entries must be regular files or directories.",
                    path=path.relative_to(root).as_posix(),
                )
    standard_roots = {
        "manifest.json",
        "checksums.sha256",
        "content/",
        "provenance/",
        "assets/content/",
        "assets/evidence/pages/",
        "assets/sources/",
        "annotations/",
        "validation/",
    }
    for package_path in files:
        if not any(
            package_path == prefix or prefix.endswith("/") and package_path.startswith(prefix)
            for prefix in standard_roots
        ):
            state.error(
                "manifest_schema",
                "unexpected_package_path",
                "File is outside the standard P2H package structure.",
                path=package_path,
            )
    return files


def _safe_package_path(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    resolved = (root / pure).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _check_required(root: Path, files: set[str], state: State) -> None:
    required = {
        "manifest.json",
        "content/document.xml",
        "provenance/pages.jsonl",
        "provenance/elements.jsonl",
        "provenance/omissions.jsonl",
        "validation/report.json",
        "checksums.sha256",
    }
    for path in sorted(required - files):
        state.error(
            "manifest_schema", "required_file_missing", "Required package file is missing.", path=path
        )
    for path in sorted(files):
        if path.endswith((".json", ".jsonl", ".xml")) or path == "checksums.sha256":
            inspect_text(root / path, path, state, "manifest_schema")


def _check_manifest(root: Path, state: State, schema_dir: Path) -> dict[str, Any] | None:
    path = root / "manifest.json"
    if not path.is_file():
        return None
    value = load_json(path, "manifest.json", state, "manifest_schema")
    if not isinstance(value, dict):
        state.error(
            "manifest_schema",
            "manifest_not_object",
            "manifest.json must contain an object.",
            path="manifest.json",
        )
        return None
    schema_valid = _validate_schema(
        value, "manifest.schema.json", "manifest.json", None, state, "manifest_schema"
    )
    if not schema_valid:
        return None
    sources = value.get("sources", [])
    ids = [s.get("id") for s in sources if isinstance(s, dict)]
    if len(ids) != len(set(ids)):
        state.error(
            "manifest_schema", "duplicate_source_id", "Source IDs must be unique.", path="manifest.json"
        )
    if sources and (sources[0].get("id"), sources[0].get("role")) != ("src-001", "primary"):
        state.error(
            "manifest_schema",
            "primary_source_order",
            "The first and primary source must be src-001.",
            path="manifest.json",
        )
    if isinstance(sources, list):
        primary = [s for s in sources if isinstance(s, dict) and s.get("role") == "primary"]
        if len(primary) == 1 and isinstance(value.get("package_id"), str):
            namespace = uuid.UUID("6d4d259c-105b-5fee-a87a-efd4ad4d9bf8")
            expected = (
                f"urn:uuid:{uuid.uuid5(namespace, 'paper2html-package:0.1:' + str(primary[0].get('sha256')))}"
            )
            if value["package_id"] != expected:
                state.error(
                    "manifest_schema",
                    "package_id_mismatch",
                    "package_id does not match the required UUIDv5 derivation.",
                    path="manifest.json",
                )
    annotations_exist = (root / "annotations").is_dir()
    if annotations_exist != ("annotations" in value):
        state.error(
            "manifest_schema",
            "annotation_manifest_mismatch",
            "annotations directory and manifest entry must either both exist or both be absent.",
            path="manifest.json",
        )
    state.pass_if_not_failed("manifest_schema")
    return value


def _load_record_file(root: Path, path: str, schema: str, state: State, check: str) -> list[dict[str, Any]]:
    target = root / path
    if not target.is_file():
        return []
    output: list[dict[str, Any]] = []
    for line, value in load_jsonl(target, path, state, check):
        if not isinstance(value, dict):
            state.error(
                check, "jsonl_record_not_object", "JSONL record must be an object.", path=path, line=line
            )
            continue
        if not _validate_schema(value, schema, path, line, state, check):
            continue
        value["__line__"] = line
        output.append(value)
    return output


def _xml_parser(xml_xsd: Path | None = None) -> etree.XMLParser:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    if xml_xsd:
        parser.resolvers.add(LockedResolver(xml_xsd))
    return parser


def _check_xml(
    root: Path,
    manifest: dict[str, Any] | None,
    state: State,
    manager: ResourceManager,
    katex_command: str | None,
) -> etree._ElementTree | None:
    path = root / "content/document.xml"
    if not path.is_file():
        return None
    try:
        tree = etree.parse(str(path), _xml_parser())
        state.checks["xml_well_formed"] = "passed"
    except (etree.XMLSyntaxError, OSError) as exc:
        state.error(
            "xml_well_formed",
            "xml_syntax",
            str(exc),
            path="content/document.xml",
            line=getattr(exc, "lineno", 1) or 1,
        )
        return None
    root_el = tree.getroot()
    profile = manifest.get("document", {}).get("profile") if manifest else None
    expected_root = "article" if profile == "jats-1.3" else "book" if profile == "bits-2.1" else None
    if expected_root and root_el.tag != expected_root:
        state.error(
            "cross_references",
            "manifest_xml_root_mismatch",
            f"Profile {profile} requires root {expected_root}.",
            path="content/document.xml",
        )
    if manifest:
        doc = manifest.get("document", {})
        if root_el.get("id") != doc.get("id"):
            state.error(
                "cross_references",
                "manifest_xml_id_mismatch",
                "XML root id differs from manifest document.id.",
                path="content/document.xml",
            )
        if root_el.get("{http://www.w3.org/XML/1998/namespace}lang") != doc.get("language"):
            state.error(
                "cross_references",
                "manifest_xml_language_mismatch",
                "XML language differs from manifest document.language.",
                path="content/document.xml",
            )
        type_ = doc.get("type")
        if type_ == "article" and root_el.tag != "article" or type_ == "book" and root_el.tag != "book":
            state.error(
                "cross_references",
                "manifest_document_type_mismatch",
                "Manifest document type contradicts XML root.",
                path="content/document.xml",
            )
    if profile not in {"jats-1.3", "bits-2.1"}:
        state.error(
            "jats_bits_schema",
            "jats_bits_profile_unknown",
            "manifest document.profile must be valid before selecting an upstream XSD.",
            path="manifest.json",
        )
        inferred_profile = (
            "jats-1.3" if root_el.tag == "article" else "bits-2.1" if root_el.tag == "book" else None
        )
    else:
        inferred_profile = profile
    if inferred_profile:
        try:
            entry = manager.xsd_entrypoint(inferred_profile)
            xml_item = manager._locked("w3c-xml-namespace-xsd")
            xml_xsd = manager.fetch(xml_item)
            parser = _xml_parser(xml_xsd)
            xsd_tree = etree.parse(str(entry), parser)
            xsd = etree.XMLSchema(xsd_tree)
            if not xsd.validate(tree):
                for error in xsd.error_log:
                    state.error(
                        "jats_bits_schema",
                        "jats_bits_schema_violation",
                        error.message,
                        path="content/document.xml",
                        line=max(1, error.line),
                    )
            state.pass_if_not_failed("jats_bits_schema")
        except Exception as exc:
            state.operational_error = True
            state.error(
                "jats_bits_schema",
                "jats_bits_engine_error",
                f"Locked JATS/BITS validation could not run: {exc}",
                path="content/document.xml",
            )
    _check_schematron(tree, state, manager)
    ids: dict[str, etree._Element] = {}
    for element in tree.iter():
        id_ = element.get("id")
        if id_:
            if id_ in ids:
                state.error(
                    "id_uniqueness",
                    "duplicate_xml_id",
                    f"Duplicate XML id {id_!r}.",
                    path="content/document.xml",
                    element_id=id_,
                )
            ids[id_] = element
    state.pass_if_not_failed("id_uniqueness")
    _check_xml_links(root, tree, ids, state)
    _check_tables_and_formulas(tree, state, katex_command)
    return tree


def _check_schematron(tree: etree._ElementTree, state: State, manager: ResourceManager) -> None:
    try:
        pipeline = manager.schxslt_pipeline()
        with tempfile.TemporaryDirectory(prefix="p2h-schxslt-") as temp:
            compiled = Path(temp) / "profile.xsl"
            svrl = Path(temp) / "result.svrl"
            with PySaxonProcessor(license=False) as processor:
                xslt = processor.new_xslt30_processor()
                executable = xslt.compile_stylesheet(stylesheet_file=str(pipeline))
                executable.set_parameter("phase", processor.make_string_value("full"))
                executable.transform_to_file(
                    source_file=str(manager.schema_dir / "p2h-profile.sch"), output_file=str(compiled)
                )
                validator = xslt.compile_stylesheet(stylesheet_file=str(compiled))
                validator.transform_to_file(source_file=str(tree.docinfo.URL), output_file=str(svrl))
            result = etree.parse(str(svrl))
            ns = {"svrl": "http://purl.oclc.org/dsdl/svrl"}
            for node in result.xpath("//svrl:failed-assert", namespaces=ns):
                message = " ".join("".join(node.itertext()).split())
                state.error("p2h_profile", "p2h_profile_violation", message, path="content/document.xml")
            for node in result.xpath("//svrl:successful-report", namespaces=ns):
                message = " ".join("".join(node.itertext()).split())
                state.warning("p2h_profile_warning", message, path="content/document.xml")
        state.pass_if_not_failed("p2h_profile")
    except Exception as exc:
        state.operational_error = True
        state.error(
            "p2h_profile",
            "schematron_engine_error",
            f"P2H Schematron could not run: {exc}",
            path="content/document.xml",
        )


def _check_xml_links(
    root: Path, tree: etree._ElementTree, ids: dict[str, etree._Element], state: State
) -> None:
    for element in tree.iter():
        rid = element.get("rid")
        if rid:
            for target in rid.split():
                if target not in ids:
                    state.error(
                        "cross_references",
                        "xref_target_missing",
                        f"rid target {target!r} does not exist.",
                        path="content/document.xml",
                        element_id=element.get("id"),
                    )
        href = element.get(XLINK)
        if href:
            if not href.startswith("../assets/content/") or ".." in href[3:].split("/"):
                state.error(
                    "cross_references",
                    "xml_resource_path_invalid",
                    f"Invalid xlink:href {href!r}.",
                    path="content/document.xml",
                )
                continue
            package_path = href[3:]
            resource_target = _safe_package_path(root, package_path)
            if resource_target is None or not resource_target.is_file():
                state.error(
                    "cross_references",
                    "xml_resource_missing",
                    f"Resource {package_path!r} does not exist.",
                    path="content/document.xml",
                )
            elif package_path.endswith(".png"):
                _check_png(resource_target, package_path, None, state)
            if element.tag == "media" and not (element.get("mimetype") or element.get("mime-subtype")):
                state.error(
                    "cross_references",
                    "media_mime_missing",
                    "media elements must declare MIME type.",
                    path="content/document.xml",
                )
    state.pass_if_not_failed("cross_references")


def _check_tables_and_formulas(
    tree: etree._ElementTree, state: State, katex_command: str | None = None
) -> None:
    formulas = list(tree.xpath("//inline-formula/tex-math | //disp-formula/tex-math"))
    if formulas:
        command = shlex.split(katex_command) if katex_command else None
        if not command or shutil.which(command[0]) is None:
            state.operational_error = True
            state.error(
                "cross_references",
                "formula_parser_unavailable",
                "Formulae exist but no executable KaTeX-compatible parser was configured.",
                path="content/document.xml",
            )
        else:
            for tex in formulas:
                try:
                    completed = subprocess.run(
                        command,
                        input="".join(tex.itertext()),
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    state.operational_error = True
                    state.error(
                        "cross_references",
                        "formula_parser_engine_error",
                        f"KaTeX-compatible parser could not run: {exc}",
                        path="content/document.xml",
                        element_id=tex.getparent().get("id") if tex.getparent() is not None else None,
                    )
                    continue
                if completed.returncode:
                    state.error(
                        "cross_references",
                        "formula_parse_error",
                        completed.stderr.strip() or "KaTeX-compatible parsing failed.",
                        path="content/document.xml",
                        element_id=tex.getparent().get("id") if tex.getparent() is not None else None,
                    )
    for table in tree.xpath("//table-wrap[not(@specific-use='image-only')]/table"):
        occupied: dict[tuple[int, int], bool] = {}
        rows = table.xpath(".//tr")
        row_widths: list[int] = []
        for row_index, row in enumerate(rows):
            column = 0
            for cell in row.xpath("./th|./td"):
                while occupied.get((row_index, column)):
                    column += 1
                try:
                    rowspan = int(cell.get("rowspan", "1"))
                    colspan = int(cell.get("colspan", "1"))
                    if rowspan < 1 or colspan < 1:
                        raise ValueError
                except ValueError:
                    state.error(
                        "cross_references",
                        "table_span_invalid",
                        "rowspan/colspan must be positive integers.",
                        path="content/document.xml",
                        element_id=cell.get("id"),
                    )
                    continue
                for r in range(row_index, row_index + rowspan):
                    for c in range(column, column + colspan):
                        if occupied.get((r, c)):
                            state.error(
                                "cross_references",
                                "table_cell_overlap",
                                "Table cell spans overlap.",
                                path="content/document.xml",
                                element_id=cell.get("id"),
                            )
                        occupied[(r, c)] = True
                column += colspan
            row_widths.append(column)
        if row_widths and len(set(row_widths)) != 1:
            state.error(
                "cross_references",
                "table_row_width_mismatch",
                "Table rows do not resolve to a consistent column count.",
                path="content/document.xml",
            )


def _check_pages(
    root: Path, manifest: dict[str, Any] | None, records: list[dict[str, Any]], state: State
) -> dict[tuple[str, int], dict[str, Any]]:
    mapping: dict[tuple[str, int], dict[str, Any]] = {}
    by_source: dict[str, list[int]] = {}
    logical_per_source: set[tuple[str, str]] = set()
    for record in records:
        source_id, page = record.get("source_id"), record.get("physical_page")
        if not isinstance(source_id, str) or not isinstance(page, int):
            continue
        key = (source_id, page)
        if key in mapping:
            state.error(
                "asset_integrity",
                "duplicate_physical_page",
                "Duplicate source/page record.",
                path="provenance/pages.jsonl",
                source_id=source_id,
                physical_page=page,
            )
        mapping[key] = record
        by_source.setdefault(source_id, []).append(page)
        logical = record.get("logical_page_id")
        if isinstance(logical, str):
            lkey = (source_id, logical)
            if lkey in logical_per_source:
                state.error(
                    "asset_integrity",
                    "logical_page_conflict",
                    "A logical page occurs more than once in one source.",
                    source_id=source_id,
                    physical_page=page,
                )
            logical_per_source.add(lkey)
        expected_image = f"assets/evidence/pages/{source_id}/page-{page:06d}.png"
        if record.get("image") != expected_image:
            state.error(
                "asset_integrity",
                "page_image_path_mismatch",
                f"Expected {expected_image}.",
                path="provenance/pages.jsonl",
                source_id=source_id,
                physical_page=page,
            )
        if record.get("ocr_status") == "failed":
            state.error(
                "asset_integrity",
                "ocr_failed",
                "A conforming package cannot contain failed OCR.",
                source_id=source_id,
                physical_page=page,
            )
        image_path = _safe_package_path(root, record.get("image"))
        if image_path is None or not image_path.is_file():
            state.error(
                "asset_integrity",
                "page_image_missing",
                "Page image is missing.",
                source_id=source_id,
                physical_page=page,
            )
        else:
            _check_png(image_path, str(record.get("image")), record, state)
    declared_images = {str(record.get("image")) for record in records}
    actual_images = {
        path.relative_to(root).as_posix()
        for path in (root / "assets/evidence/pages").glob("**/*")
        if path.is_file()
    }
    for extra in sorted(actual_images - declared_images):
        state.error(
            "asset_integrity",
            "undeclared_page_image",
            "Evidence page PNG has no pages.jsonl record.",
            path=extra,
        )
    if manifest:
        sources = {
            source["id"]: source
            for source in manifest.get("sources", [])
            if isinstance(source, dict) and isinstance(source.get("id"), str)
        }
        for source_id, source in sources.items():
            actual = sorted(by_source.get(source_id, []))
            expected = list(range(1, int(source.get("page_count", 0)) + 1))
            if actual != expected:
                state.error(
                    "asset_integrity",
                    "page_sequence",
                    f"Pages must be continuous from 1 through {source.get('page_count')}.",
                    source_id=source_id,
                )
            if source_id == "src-001":
                for page in expected:
                    page_record = mapping.get((source_id, page))
                    if page_record and page_record.get("logical_page_id") != f"lp-{page:06d}":
                        state.error(
                            "asset_integrity",
                            "primary_logical_page_id",
                            "Primary logical_page_id must derive from physical page.",
                            source_id=source_id,
                            physical_page=page,
                        )
            embedded = source.get("embedded_path")
            if isinstance(embedded, str):
                embedded_file = _safe_package_path(root, embedded)
                if embedded_file is None or not embedded_file.is_file():
                    state.error(
                        "asset_integrity",
                        "embedded_source_missing",
                        "Manifest embedded source is missing.",
                        path=embedded,
                        source_id=source_id,
                    )
                elif embedded_file.stat().st_size != source.get("size") or sha256_file(
                    embedded_file
                ) != source.get("sha256"):
                    state.error(
                        "asset_integrity",
                        "embedded_source_integrity",
                        "Embedded source size or SHA-256 differs from manifest.",
                        path=embedded,
                        source_id=source_id,
                    )
        unknown = set(by_source) - set(sources)
        for source_id in sorted(unknown):
            state.error(
                "asset_integrity",
                "unknown_page_source",
                "Page references a source absent from manifest.",
                source_id=source_id,
            )
    state.pass_if_not_failed("asset_integrity")
    return mapping


def _check_png(path: Path, package_path: str, record: dict[str, Any] | None, state: State) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.format != "PNG":
                state.error(
                    "asset_integrity", "image_not_png", "Required PNG resource is not PNG.", path=package_path
                )
                return
            if record:
                if image.size != (record.get("image_width_px"), record.get("image_height_px")):
                    state.error(
                        "asset_integrity",
                        "page_pixel_size_mismatch",
                        "Decoded PNG dimensions differ from pages.jsonl.",
                        path=package_path,
                    )
                expected_w = float(record.get("width_pt", 0)) * 300 / 72
                expected_h = float(record.get("height_pt", 0)) * 300 / 72
                if abs(image.width - expected_w) > 1 or abs(image.height - expected_h) > 1:
                    state.error(
                        "asset_integrity",
                        "page_dpi_geometry",
                        "PNG pixel dimensions are inconsistent with 300 DPI point geometry.",
                        path=package_path,
                    )
                dpi = image.info.get("dpi")
                if dpi and (abs(dpi[0] - 300) > 1 or abs(dpi[1] - 300) > 1):
                    state.error(
                        "asset_integrity",
                        "png_dpi_metadata",
                        "PNG resolution metadata is not 300 DPI.",
                        path=package_path,
                    )
            if image.mode not in {"RGB", "RGBA"}:
                state.error(
                    "asset_integrity",
                    "png_not_srgb_compatible",
                    "PNG must use an sRGB-compatible RGB pixel mode.",
                    path=package_path,
                )
    except (UnidentifiedImageError, OSError) as exc:
        state.error("asset_integrity", "image_decode", f"Image cannot be decoded: {exc}", path=package_path)


def _bbox_valid(bbox: Any) -> bool:
    return (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in bbox)
        and bbox[0] < bbox[2]
        and bbox[1] < bbox[3]
    )


def _check_elements(
    root: Path,
    manifest: dict[str, Any] | None,
    tree: etree._ElementTree | None,
    records: list[dict[str, Any]],
    pages: dict[tuple[str, int], dict[str, Any]],
    checksums: dict[str, str],
    state: State,
) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    order_values: list[int] = []
    xml_by_id = {e.get("id"): e for e in tree.iter() if e.get("id")} if tree else {}
    sources = {s.get("id"): s for s in manifest.get("sources", []) if isinstance(s, dict)} if manifest else {}
    for record in records:
        element_id = record.get("element_id")
        if not isinstance(element_id, str):
            continue
        if element_id in by_id:
            state.error(
                "element_provenance",
                "duplicate_element_provenance",
                "Element has more than one provenance record.",
                element_id=element_id,
            )
        by_id[element_id] = record
        if element_id not in xml_by_id:
            state.error(
                "element_provenance",
                "provenance_xml_id_missing",
                "Provenance points to an absent XML id.",
                element_id=element_id,
            )
        expected_xpath = f"//*[@id='{element_id}']"
        if record.get("xml_path") != expected_xpath:
            state.error(
                "element_provenance",
                "xml_path_noncanonical",
                f"xml_path must be {expected_xpath!r}.",
                element_id=element_id,
            )
        order = record.get("reading_order")
        if isinstance(order, int):
            order_values.append(order)
        seen_pages: set[tuple[str, int]] = set()
        element_sources = record.get("sources", [])
        source_keys = [
            (str(item.get("source_id", "")), int(item.get("physical_page", 0)))
            for item in element_sources
            if isinstance(item, dict) and isinstance(item.get("physical_page", 0), int)
        ]
        if source_keys != sorted(source_keys):
            state.error(
                "element_provenance",
                "element_sources_order",
                "Element sources must be sorted by source_id and physical_page.",
                element_id=element_id,
            )
        all_candidate_methods: set[str] = set()
        for source_record in element_sources:
            source_id, page = source_record.get("source_id"), source_record.get("physical_page")
            key = (source_id, page)
            if key in seen_pages:
                state.error(
                    "element_provenance",
                    "duplicate_element_page",
                    "Element repeats a source/page pair.",
                    element_id=element_id,
                    source_id=source_id,
                    physical_page=page,
                )
            seen_pages.add(key)
            page_record = pages.get(key)
            if not page_record:
                state.error(
                    "element_provenance",
                    "element_page_missing",
                    "Element points to an absent page record.",
                    element_id=element_id,
                    source_id=source_id,
                    physical_page=page,
                )
            elif source_record.get("logical_page_id") != page_record.get(
                "logical_page_id"
            ) or source_record.get("page_image") != page_record.get("image"):
                state.error(
                    "element_provenance",
                    "element_page_reference_mismatch",
                    "Element page/logical/image fields disagree with pages.jsonl.",
                    element_id=element_id,
                    source_id=source_id,
                    physical_page=page,
                )
            regions = source_record.get("regions", [])
            region_keys = [
                (region.get("bbox", [0, 0])[1], region.get("bbox", [0, 0])[0])
                for region in regions
                if isinstance(region.get("bbox"), list) and len(region.get("bbox")) >= 2
            ]
            if region_keys != sorted(region_keys):
                state.error(
                    "element_provenance",
                    "element_regions_order",
                    "Regions must be sorted top-to-bottom, then left-to-right.",
                    element_id=element_id,
                )
            for region in regions:
                bbox = region.get("bbox")
                if not _bbox_valid(bbox):
                    state.error(
                        "element_provenance",
                        "bbox_order",
                        "bbox must satisfy x0<x1 and y0<y1 inside [0,1].",
                        element_id=element_id,
                    )
                polygon = region.get("polygon")
                if polygon and _bbox_valid(bbox):
                    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
                    envelope = [min(xs), min(ys), max(xs), max(ys)]
                    if any(abs(a - b) > 1e-6 for a, b in zip(envelope, bbox, strict=True)):
                        state.error(
                            "element_provenance",
                            "polygon_bbox_mismatch",
                            "bbox is not the polygon's minimum envelope.",
                            element_id=element_id,
                        )
            source = sources.get(source_id, {})
            methods = {c.get("method") for c in source_record.get("candidates", [])}
            all_candidate_methods.update(str(method) for method in methods if method)
            tag = xml_by_id[element_id].tag if element_id in xml_by_id else None
            if tag in TEXT_TAGS:
                if "ocr" not in methods:
                    state.error(
                        "element_provenance",
                        "ocr_candidate_missing",
                        "Text element lacks an OCR candidate.",
                        element_id=element_id,
                        source_id=source_id,
                        physical_page=page,
                    )
                native_required = source.get("source_class") == "born-digital" or (
                    source.get("source_class") == "hybrid"
                    and "native-pdf" in source.get("extraction_modes", [])
                )
                if native_required and "native-pdf" not in methods:
                    state.error(
                        "element_provenance",
                        "native_candidate_missing",
                        "Born-digital text element lacks a native-pdf candidate.",
                        element_id=element_id,
                        source_id=source_id,
                        physical_page=page,
                    )
        if record.get("decision", {}).get("method") == "manual" and not any(
            r.get("method") == "manual" for r in record.get("revisions", [])
        ):
            state.error(
                "element_provenance",
                "manual_revision_missing",
                "Manual decision requires a manual revision.",
                element_id=element_id,
            )
        decision_method = record.get("decision", {}).get("method")
        if decision_method in {"native-pdf", "ocr"} and decision_method not in all_candidate_methods:
            state.error(
                "element_provenance",
                "decision_candidate_missing",
                "Decision method has no matching extraction candidate.",
                element_id=element_id,
            )
        revisions = record.get("revisions", [])
        timestamps = [str(revision.get("timestamp", "")) for revision in revisions]
        if timestamps != sorted(timestamps):
            state.error(
                "element_provenance",
                "revision_order",
                "Revisions must be sorted by timestamp.",
                element_id=element_id,
            )
        for revision in revisions:
            for evidence in revision.get("evidence", []):
                evidence_page = pages.get((evidence.get("source_id"), evidence.get("physical_page")))
                if not evidence_page or evidence_page.get("image") != evidence.get("page_image"):
                    state.error(
                        "element_provenance",
                        "revision_evidence_page_mismatch",
                        "Revision evidence disagrees with pages.jsonl.",
                        element_id=element_id,
                    )
                if not _bbox_valid(evidence.get("bbox")):
                    state.error(
                        "element_provenance",
                        "revision_evidence_bbox",
                        "Revision evidence bbox is invalid.",
                        element_id=element_id,
                    )
        for previous, following in zip(revisions, revisions[1:], strict=False):
            if previous.get("after") != following.get("before"):
                state.error(
                    "element_provenance",
                    "revision_chain_broken",
                    "Adjacent revisions do not form a chain.",
                    element_id=element_id,
                )
        if revisions and element_id in xml_by_id:
            final_text = "".join(xml_by_id[element_id].itertext())
            if revisions[-1].get("after") != final_text:
                state.error(
                    "element_provenance",
                    "revision_final_mismatch",
                    "Last revision does not match final XML text.",
                    element_id=element_id,
                )
        for resource in record.get("resources", []):
            resource_path = resource.get("path")
            target = _safe_package_path(root, resource_path)
            if target is None or not target.is_file():
                state.error(
                    "asset_integrity",
                    "element_resource_missing",
                    "Element resource is missing.",
                    element_id=element_id,
                )
            elif sha256_file(target) != resource.get("sha256") or checksums.get(
                resource_path
            ) != resource.get("sha256"):
                state.error(
                    "asset_integrity",
                    "element_resource_hash",
                    "Element resource hash disagrees with file/checksums.",
                    path=resource_path,
                    element_id=element_id,
                )
            guessed = mimetypes.guess_type(str(resource_path))[0]
            if guessed and guessed != resource.get("media_type"):
                state.error(
                    "asset_integrity",
                    "element_resource_mime",
                    f"Declared MIME differs from extension-derived {guessed}.",
                    path=resource_path,
                    element_id=element_id,
                )
            if resource.get("source_id") is not None and resource.get("source_id") not in sources:
                state.error(
                    "asset_integrity",
                    "element_resource_source_missing",
                    "Element resource refers to an unknown source.",
                    path=resource_path,
                    element_id=element_id,
                )
    required = [e.get("id") for e in tree.iter() if e.tag in ADDRESSABLE_TAGS and e.get("id")] if tree else []
    for element_id in required:
        if element_id not in by_id:
            state.error(
                "element_provenance",
                "element_provenance_missing",
                "Addressable XML element lacks provenance.",
                element_id=element_id,
            )
    for element_id in set(by_id) - set(required):
        if element_id in xml_by_id:
            state.warning(
                "provenance_for_nonrequired_element",
                "Provenance exists for an element not required by the profile.",
                element_id=element_id,
            )
    expected_orders = list(range(1, len(records) + 1))
    if sorted(order_values) != expected_orders:
        state.error(
            "element_provenance",
            "reading_order_sequence",
            "reading_order must be unique and continuous from 1.",
            path="provenance/elements.jsonl",
        )
    actual_order = [
        r.get("element_id") for r in sorted(records, key=lambda r: r.get("reading_order", 10**12))
    ]
    if tree and actual_order != required:
        state.error(
            "element_provenance",
            "reading_order_xml_mismatch",
            "reading_order differs from addressable XML document order.",
            path="provenance/elements.jsonl",
        )
    state.pass_if_not_failed("element_provenance")


def _check_omissions(
    records: list[dict[str, Any]], pages: dict[tuple[str, int], dict[str, Any]], state: State
) -> None:
    ids: set[str] = set()
    for record in records:
        id_ = record.get("id")
        if id_ in ids:
            state.error(
                "element_provenance",
                "duplicate_omission_id",
                "Omission IDs must be unique.",
                path="provenance/omissions.jsonl",
            )
        if isinstance(id_, str):
            ids.add(id_)
        source_id = record.get("source_id")
        physical_page = record.get("physical_page")
        page = (
            pages.get((source_id, physical_page))
            if isinstance(source_id, str) and isinstance(physical_page, int)
            else None
        )
        if (
            not page
            or page.get("logical_page_id") != record.get("logical_page_id")
            or page.get("image") != record.get("page_image")
        ):
            state.error(
                "element_provenance",
                "omission_page_mismatch",
                "Omission page reference disagrees with pages.jsonl.",
                path="provenance/omissions.jsonl",
            )
        if not _bbox_valid(record.get("bbox")):
            state.error(
                "element_provenance",
                "omission_bbox_order",
                "Omission bbox is invalid.",
                path="provenance/omissions.jsonl",
            )


def _check_annotations(root: Path, manifest: dict[str, Any] | None, xml_ids: set[str], state: State) -> None:
    if not manifest or "annotations" not in manifest:
        return
    path = "annotations/index.json"
    index = load_json(root / path, path, state, "cross_references")
    if not isinstance(index, dict):
        return
    _validate_schema(index, "annotation-index.schema.json", path, None, state, "cross_references")
    ids: set[str] = set()
    paths: set[str] = set()
    for layer in index.get("layers", []):
        if layer.get("id") in ids or layer.get("path") in paths:
            state.error(
                "cross_references",
                "annotation_layer_duplicate",
                "Annotation layer IDs and paths must be unique.",
                path=path,
            )
        ids.add(layer.get("id"))
        paths.add(layer.get("path"))
        records = _load_record_file(
            root, layer.get("path", ""), "annotation.schema.json", state, "cross_references"
        )
        for record in records:
            if record.get("target_id") not in xml_ids:
                state.error(
                    "cross_references",
                    "annotation_target_missing",
                    "Annotation target does not exist in XML.",
                    path=layer.get("path"),
                    element_id=record.get("target_id"),
                )
            if record.get("kind") != layer.get("kind") or record.get("language") != layer.get("language"):
                state.error(
                    "cross_references",
                    "annotation_layer_mismatch",
                    "Annotation kind/language differs from index.",
                    path=layer.get("path"),
                )
            fragment = record.get("content_xml")
            if fragment:
                try:
                    wrapper = etree.fromstring(f"<wrapper>{fragment}</wrapper>".encode(), _xml_parser())
                    if any(e.tag not in INLINE_ANNOTATION_TAGS for e in wrapper.iterdescendants()):
                        state.error(
                            "cross_references",
                            "annotation_xml_element",
                            "content_xml contains a disallowed element.",
                            path=layer.get("path"),
                        )
                except etree.XMLSyntaxError as exc:
                    state.error("cross_references", "annotation_xml_syntax", str(exc), path=layer.get("path"))


def _read_checksums(root: Path, state: State) -> dict[str, str]:
    path = root / "checksums.sha256"
    text = inspect_text(path, "checksums.sha256", state, "checksum_integrity") if path.is_file() else None
    entries: dict[str, str] = {}
    previous: bytes | None = None
    if text is None:
        return entries
    for number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([a-z0-9._/-]+)", line)
        if not match:
            state.error(
                "checksum_integrity",
                "checksum_syntax",
                "Invalid checksum line syntax.",
                path="checksums.sha256",
                line=number,
            )
            continue
        digest, package_path = match.groups()
        encoded = package_path.encode()
        if previous is not None and encoded <= previous:
            state.error(
                "checksum_integrity",
                "checksum_order",
                "Checksum paths must be strictly sorted by UTF-8 bytes.",
                path="checksums.sha256",
                line=number,
            )
        previous = encoded
        if package_path in entries:
            state.error(
                "checksum_integrity",
                "checksum_duplicate",
                "Duplicate checksum path.",
                path="checksums.sha256",
                line=number,
            )
        entries[package_path] = digest
    return entries


def _check_checksums(
    root: Path, files: set[str], entries: dict[str, str], state: State, ignore_report_mismatch: bool
) -> None:
    expected = files - {"checksums.sha256"}
    for path in sorted(expected - set(entries)):
        state.error(
            "checksum_integrity", "checksum_missing", "File is absent from checksums.sha256.", path=path
        )
    for path in sorted(set(entries) - expected):
        state.error(
            "checksum_integrity",
            "checksum_extra",
            "Checksum points to an absent or forbidden file.",
            path=path,
        )
    for path in sorted(expected & set(entries)):
        if ignore_report_mismatch and path == "validation/report.json":
            continue
        if sha256_file(root / path) != entries[path]:
            state.error(
                "checksum_integrity", "checksum_mismatch", "SHA-256 does not match file bytes.", path=path
            )
    state.pass_if_not_failed("checksum_integrity")


def _check_existing_report(root: Path, state: State, writing: bool) -> None:
    path = root / "validation/report.json"
    if not path.is_file():
        return
    value = load_json(path, "validation/report.json", state, "manifest_schema")
    if value is not None:
        errors = _schema_validate(value, "validation-report.schema.json", _ACTIVE_SCHEMA_DIR.get())
        for message in errors:
            if writing:
                state.warning("existing_report_schema", message, path="validation/report.json")
            else:
                state.error(
                    "manifest_schema", "validation_report_schema", message, path="validation/report.json"
                )


def _check_coverage(
    path: Path | None, pages: dict[tuple[str, int], dict[str, Any]], state: State
) -> dict[str, Any] | None:
    if path is None:
        state.errors.append(
            Finding(
                "page_coverage_not_audited",
                "No independent page-object coverage evidence was supplied.",
            )
        )
        state.checks["page_coverage"] = "partial"
        return None
    try:
        evidence = json.loads(path.read_text())
        if (
            not isinstance(evidence, dict)
            or not isinstance(evidence.get("engine"), str)
            or not isinstance(evidence.get("engine_version"), str)
        ):
            raise ValueError("engine and engine_version are required")
        reviewed = {(p["source_id"], p["physical_page"]) for p in evidence.get("reviewed_pages", [])}
        if reviewed != set(pages):
            state.error(
                "page_coverage",
                "coverage_pages_incomplete",
                "Coverage evidence does not review exactly all package pages.",
            )
        for item in evidence.get("uncovered_objects", []):
            state.error(
                "page_coverage",
                "uncovered_visible_object",
                item.get("message", "An uncovered visible object was detected."),
                source_id=item.get("source_id"),
                physical_page=item.get("physical_page"),
            )
        state.pass_if_not_failed("page_coverage")
        return evidence
    except Exception as exc:
        state.error("page_coverage", "coverage_evidence_invalid", f"Cannot use coverage evidence: {exc}")
        return None


def _make_report(state: State, coverage: dict[str, Any] | None) -> dict[str, Any]:
    valid = not state.errors and all(state.checks[name] == "passed" for name in CHECKS)
    report: dict[str, Any] = {
        "$schema": SCHEMA_URL,
        "format": "paper2html-validation-report",
        "format_version": "0.1",
        "valid": valid,
        "validated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "checks": state.checks,
        "statistics": state.statistics,
        "errors": [finding.as_dict() for finding in state.errors],
        "warnings": [finding.as_dict() for finding in state.warnings],
    }
    if coverage:
        report["x-page-coverage-auditor"] = {
            "engine": coverage["engine"],
            "engine_version": coverage["engine_version"],
        }
    return report


def validate_package(
    root: Path, options: ValidationOptions | None = None, *, writing_report: bool = False
) -> ValidationResult:
    options = options or ValidationOptions()
    root = root.resolve()
    schema_dir = (options.schema_dir or _default_schema_dir()).resolve()
    cache_dir = (options.cache_dir or _default_cache_dir()).resolve()
    _ACTIVE_SCHEMA_DIR.set(schema_dir)
    state = State()
    try:
        manager = ResourceManager(schema_dir, cache_dir, options.allow_network)
        files = _scan_filesystem(root, state)
        _check_required(root, files, state)
        manifest = _check_manifest(root, state, schema_dir)
        _check_existing_report(root, state, writing_report)
        checksums = _read_checksums(root, state)
        _check_checksums(root, files, checksums, state, ignore_report_mismatch=writing_report)
        pages = _load_record_file(
            root, "provenance/pages.jsonl", "page.schema.json", state, "asset_integrity"
        )
        elements = _load_record_file(
            root, "provenance/elements.jsonl", "element.schema.json", state, "element_provenance"
        )
        omissions = _load_record_file(
            root, "provenance/omissions.jsonl", "omission.schema.json", state, "element_provenance"
        )
        tree = _check_xml(root, manifest, state, manager, options.katex_command)
        page_map = _check_pages(root, manifest, pages, state)
        _check_elements(root, manifest, tree, elements, page_map, checksums, state)
        _check_omissions(omissions, page_map, state)
        xml_ids = {str(e.get("id")) for e in tree.iter() if e.get("id")} if tree else set()
        _check_annotations(root, manifest, xml_ids, state)
        coverage = _check_coverage(options.coverage_evidence, page_map, state)
        state.statistics.update(
            {
                "sources": len(manifest.get("sources", [])) if manifest else 0,
                "pages": len(pages),
                "elements": len(elements),
                "omissions": len(omissions),
                "errors": len(state.errors),
                "warnings": len(state.warnings),
            }
        )
        report = _make_report(state, coverage)
        # The generated report itself must always satisfy the normative schema.
        report_errors = _schema_validate(report, "validation-report.schema.json", schema_dir)
        if report_errors:
            return ValidationResult(report, operational_error=True)
        return ValidationResult(report, operational_error=state.operational_error)
    except (OSError, ValueError, ResourceError, json.JSONDecodeError) as exc:
        state.error("manifest_schema", "validator_operational_error", str(exc))
        return ValidationResult(_make_report(state, None), operational_error=True)


def write_report(root: Path, result: ValidationResult) -> None:
    target = root / "validation/report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.report, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=target.parent, delete=False
    ) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    os.replace(temporary, target)
    checksum_path = root / "checksums.sha256"
    entries: dict[str, str] = {}
    if checksum_path.exists():
        for line in checksum_path.read_text().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  ([a-z0-9._/-]+)", line)
            if match:
                entries[match.group(2)] = match.group(1)
    entries["validation/report.json"] = sha256_file(target)
    content = "".join(
        f"{digest}  {path}\n" for path, digest in sorted(entries.items(), key=lambda x: x[0].encode())
    )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=checksum_path.parent, delete=False
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, checksum_path)
