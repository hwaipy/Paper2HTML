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

The persistent first case can be rerun locally with:

```sh
uv run python -m src.converter.cli \
  testdata/cases/papers/arxiv-2503-17744v1/input/2503.17744v1.pdf \
  testdata/runs/arxiv-2503-17744v1-minimal-pipeline \
  --replace
```

Useful options:

- `--replace` atomically replaces an existing output directory. Without it,
  existing output is left untouched.
- `--created-at 2026-08-12T00:00:00Z` fixes the manifest completion timestamp
  for reproducibility tests.
- `--cache-dir PATH` selects the validator's verified resource cache.
- `--json` prints the generated validation result.

## What the pipeline actually does

1. Reads every PDF page's effective geometry with Poppler.
2. Renders the untrimmed CropBox at 300 DPI to sRGB-compatible PNG.
3. Extracts native PDF text runs and bounding boxes with Poppler.
4. Independently OCRs the rendered PNGs with Apple Vision, retaining its text,
   confidence, and bounding boxes rather than copying native text.
5. Joins native font runs into visual lines, applies a small deterministic
   reading-order/paragraph heuristic, and emits one JATS 1.3 article.
6. Writes page and element provenance. Each text element has both raw native
   and OCR candidates and normalized page coordinates.
7. Writes the manifest, empty omissions file, validation report, and exhaustive
   SHA-256 list, then runs the independent validator.
8. Publishes the completed directory atomically. A failed run does not expose a
   partial result directory.

The primary-source SHA-256 determines the UUIDv5 package ID. Element IDs and
page IDs are assigned deterministically from the reconstructed reading order.
By default, `created_at` records the run time and the stored `validated_at`
matches it. With `--created-at`, both timestamps are fixed, so the complete
package including `validation/report.json` and `checksums.sha256` is
byte-reproducible for deterministic extraction engines.

## Honest current limits

This stage supports born-digital, article-like PDFs with extractable native
text. It stops on image-only scanned PDFs; adding OCR-first structure recovery
is later work.

The semantic reconstruction is intentionally minimal:

- authors, affiliations, abstract, references, citations, figures, tables, and
  formulae are not yet promoted to their full JATS element types;
- visible figure and formula content may appear only as imperfect native text;
- multi-column ordering and paragraph grouping are heuristic;
- generic non-arXiv PDFs receive transparent `Unknown publication` metadata;
- JATS 1.3 requires an ISSN slot in `journal-meta`. When the page identifies
  arXiv, the converter derives arXiv.org ISSN `2331-8422` from the fixed ISSN
  Registry record. The raw candidate remains the actual page text, while an
  automatic revision records the derivation rule, registry URL, timestamp, and
  page evidence. Unknown publications retain an explicit empty
  `specific-use="not-applicable"` placeholder without false provenance;
- no page headers, footers, or page numbers are classified as omissions yet;
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
