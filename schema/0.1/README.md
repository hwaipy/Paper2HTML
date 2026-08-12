# P2H Package 0.1 machine-readable schemas

This directory is the normative machine-readable companion to
`STRUCTURED_DOCUMENT_PACKAGE_SPEC.md` for format version 0.1.

## JSON and JSONL

- `manifest.schema.json` validates `manifest.json`.
- `page.schema.json` validates each non-empty line of `provenance/pages.jsonl`.
- `element.schema.json` validates each non-empty line of `provenance/elements.jsonl`.
- `omission.schema.json` validates each non-empty line of `provenance/omissions.jsonl`.
- `annotation-index.schema.json` validates `annotations/index.json`.
- `annotation.schema.json` validates each non-empty annotation-layer line.
- `validation-report.schema.json` validates `validation/report.json`.
- `common.schema.json` contains shared definitions and is not a package file schema.

All JSON schemas use JSON Schema Draft 2020-12. A JSONL file is not a JSON
array: validators must parse and validate every non-empty physical line as one
independent JSON value and report its 1-based line number.

## XML

Validation is deliberately layered:

1. An article must validate against NISO JATS Journal Publishing 1.3, using
   `https://jats.nlm.nih.gov/publishing/1.3/xsd/JATS-journalpublishing1-3.xsd`.
2. A book must validate against BITS 2.1, using
   `https://jats.nlm.nih.gov/extensions/bits/2.1/xsd/BITS-book2-1.xsd`.
3. Either document must then pass `p2h-profile.sch`, phase `full`.

The upstream schemas may be cached for offline operation, but a validator must
verify the cached release and must not silently substitute another JATS/BITS
version. `upstream-lock.json` pins the official archive URLs, SHA-256 digests,
and entrypoints used by P2H 0.1. The BITS XSD imports the XML namespace without
a `schemaLocation`; validators must resolve `http://www.w3.org/XML/1998/namespace`
to the locked W3C `xml.xsd`. The P2H vocabulary follows standard JATS/BITS practice and has no
default namespace; `xlink` retains its standard namespace.

## Constraints outside individual schemas

JSON Schema and Schematron validate individual files. A conforming package
validator must additionally implement the cross-file and filesystem rules in
sections 21 and 25 of the specification, including:

- unique source IDs, element IDs, omission IDs, annotation layer IDs, and
  `(source_id, physical_page)` pairs;
- source/page continuity and agreement with `page_count`;
- equality of source, page, logical-page, and image references across files;
- `bbox` ordering (`x0 < x1`, `y0 < y1`) and polygon containment;
- one provenance record per required XML element and XML-order agreement;
- source-class-dependent native/OCR candidate requirements;
- package-path resolution, MIME and image checks, coverage, and checksums;
- agreement between the manifest document type and XML root.

These are normative requirements, not optional validation enhancements.
