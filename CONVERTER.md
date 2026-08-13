# Minimal reproducible conversion pipeline

`src.converter.cli` turns one born-digital PDF into a real P2H Package 0.1
directory. It is the first executable conversion baseline, not a claim that
automatic publication-quality reconstruction is solved.

The converter is a repository tool. It does not build a wheel or install a
standalone command. The reader is a separate tool and is never copied into a
result package.

## Prerequisites

- Python 3.11 or newer and `uv`;
- Poppler commands `pdfinfo`, `pdftoppm`, and `pdftohtml`;
- macOS with Xcode command-line tools, because the first independent OCR
  backend uses Apple Vision through `xcrun swift`.

Install the repository environment once:

```sh
uv sync --extra dev
```

The locked JATS and Schematron resources are downloaded only when
`--allow-network` is present. After a successful online run they are reused
from the validator cache.

## Run

```sh
uv run python -m src.converter.cli INPUT.pdf OUTPUT_DIRECTORY --allow-network
```

`INPUT` can also be a UTF-8 JSON source descriptor. This lets a repository pin
a redistributable URL and its exact bytes without committing the PDF:

```json
{
  "format": "paper2html-pdf-source",
  "format_version": "1",
  "case_id": "arxiv-2503-17744v1",
  "url": "https://arxiv.org/pdf/2503.17744v1",
  "sha256": "10cb935f09510e179ed2e6aa5e593c853c68d48ad5316f8b0ed0e70a88f9eaa2",
  "size": 754271,
  "original_name": "2503.17744v1.pdf"
}
```

All seven fields are required and no additional fields are accepted. The URL
must be absolute HTTP(S); a permanent arXiv test must include an explicit
version such as `v1`, never a latest-version URL. The converter limits redirects,
socket timeout, and total response size, checks `%PDF-`, byte count, and SHA-256,
then atomically publishes the verified PDF to a content-addressed cache. It
records the requested URL, final URL, and content hash in the manifest source's
`x-origin`. A cache hit works offline; a miss requires `--allow-network`.

The persistent first case can be rerun locally with:

```sh
uv run python -m src.converter.cli \
  tests/golden/arxiv-2503-17744v1/source.json \
  testdata/runs/arxiv-2503-17744v1-minimal-pipeline \
  --allow-network --replace
```

Useful options:

- `--replace` atomically replaces an existing output directory. Without it,
  existing output is left untouched.
- `--created-at 2026-08-12T00:00:00Z` fixes the manifest completion timestamp
  for reproducibility tests.
- `--cache-dir PATH` selects the validator's verified resource cache.
- `--download-cache-dir PATH` selects the verified remote-PDF cache.
- `--secure-dns` uses DNS-over-HTTPS through a pinned global resolver address;
  use it when a local proxy exposes reserved synthetic DNS addresses. Every
  returned A/AAAA address is still required to be globally routable.
- `--json` prints the generated validation result.

## What the pipeline actually does

1. Reads every PDF page's effective geometry with Poppler.
2. Renders the untrimmed CropBox at 300 DPI to sRGB-compatible PNG.
3. Extracts native PDF text runs and bounding boxes with Poppler.
4. Independently OCRs the rendered PNGs with Apple Vision, retaining its text,
   confidence, and bounding boxes rather than copying native text.
5. Joins native font runs into visual lines and applies deterministic geometry,
   typography, reading-order, paragraph, front-matter, caption, section, and
   reference heuristics before emitting one JATS 1.3 article.
6. Writes page and element provenance. Each text element has both raw native
   and OCR candidates and normalized page coordinates.
7. Records excluded page numbers, unrecovered mathematical fragments, authorship
   markers, and detected-but-unstructured tables in `omissions.jsonl`.
8. Crops reliably bounded figures without their captions, writes the manifest,
   validation report, and exhaustive SHA-256 list, then runs the independent
   validator.
9. Publishes the completed directory atomically. A failed run does not expose a
   partial result directory.

The primary-source SHA-256 determines the UUIDv5 package ID. Element IDs and
page IDs are assigned deterministically from the reconstructed reading order.
By default, `created_at` records the run time and the stored `validated_at`
matches it. With `--created-at`, both timestamps are fixed, so the complete
package including `validation/report.json` and `checksums.sha256` is
byte-reproducible for deterministic extraction engines.

## Tool interfaces

The pipeline depends on the static contracts in
`src/converter/tool_interfaces.py`, rather than requiring a dynamic plugin
system. `ConversionTools` groups five independently replaceable tools:

- `PDFInspector`: PDF page count, point sizes, and rotations;
- `PageRenderer`: canonical 300 DPI evidence PNGs;
- `NativeTextExtractor`: native PDF text candidates and normalized boxes;
- `OCREngine`: independent OCR candidates over rendered pages;
- `SemanticParser`: document structure reconstructed from the source PDF and
  collected page evidence (an implementation may use either or both).

Their shared input and output values live in `src/converter/models.py`. Every
text extraction result carries its real engine name and version, which are
written to provenance instead of being inferred by the pipeline. The boundary
checks in `src/converter/tool_validation.py` reject inconsistent page counts,
page numbers, statuses, coordinates, confidence values, render locations, and
noncanonical evidence DPI before package generation.

`default_conversion_tools()` statically composes the current Poppler, Apple
Vision, and heuristic implementations. A source-code integration can replace
one or more implementations by constructing another `ConversionTools` value
and passing it to `convert_pdf(..., tools=tools)`. No registry, entry point,
runtime discovery, or CLI backend selection is involved. A replacement only
needs to satisfy the same typed input/output contract and boundary validation.

## Honest current limits

This stage supports born-digital, article-like PDFs with extractable native
text. It stops on image-only scanned PDFs; adding OCR-first structure recovery
is later work.

Semantic reconstruction is deterministic but still heuristic:

- article title, authors, affiliations, abstract, source-visible publication date,
  sections, paragraphs, reference entries, figures, and captions are promoted to
  JATS when typography and geometry provide reliable boundaries;
- author superscripts are geometrically associated with individual names and
  emitted as affiliation and equal-contribution JATS links when their layout is
  unambiguous. The original Poppler multi-author line remains the source region
  and candidate; the narrower per-name bbox is explicitly recorded as an
  approximate, automatically derived layout revision;
- detected tables and display-math regions are explicitly omitted
  with page evidence when reliable cell structure or TeX cannot be reconstructed;
  partial formula text is never emitted as if it were complete semantics;
- multi-column ordering, paragraph grouping, and document-type detection remain
  heuristic;
- generic non-arXiv PDFs receive transparent `Unknown publication` metadata;
- JATS 1.3 requires an ISSN slot in `journal-meta`. When the page identifies
  arXiv, the converter derives arXiv.org ISSN `2331-8422` from the fixed ISSN
  Registry record. The raw candidate remains the actual page text, while an
  automatic revision records the derivation rule, registry URL, timestamp, and
  page evidence. Unknown publications retain an explicit empty
  `specific-use="not-applicable"` placeholder without false provenance;
- printed page numbers and repository side stamps are excluded from body text;
- Apple Vision is the only OCR backend in this stage.

Most importantly, OCR text boxes are not a complete independent layout audit:
they cannot prove that figures, decorations, or other visible objects were not
missed. The converter therefore does not fabricate coverage evidence. Its
package is expected to have `page_coverage: partial` and `valid: false`. For the
persistent arXiv case, this remains the only failed validation layer. Unknown
publication identities have no source-backed provenance records for the
placeholder journal identity or filename-derived article ID; the validator
therefore fails `element_provenance`, and the package is not conforming, rather
than accepting fabricated page evidence.

Recheck a generated package independently with:

```sh
uv run python -m src.validator.cli OUTPUT_DIRECTORY
```

Measure semantic reconstruction separately from package validity with:

```sh
uv run python -m src.converter.quality OUTPUT_DIRECTORY --json
```

The checklist and the committed case's measured before/after baseline are in
`CONTENT_QUALITY.md`.
