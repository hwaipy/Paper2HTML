# Content quality baseline

Package validity and content reconstruction quality answer different questions.
The validator checks P2H 0.1 structure, provenance, resources, and integrity. The
content-quality report checks whether a reader receives useful article semantics.

Run the deterministic checklist on any generated package:

```sh
uv run python -m src.converter.quality PACKAGE --json
```

The command first runs the normative package validator, including schemas,
checksums, resource hashes, and PNG decoding. It then compares addressable XML
text with independent native-PDF/OCR candidates in provenance. Therefore a
changed XML or resource fails without a matching checksum, while rehashing a
changed XML does not hide a candidate mismatch. Exit code 1 means a quality or
integrity failure; exit code 2 means the check could not be executed.

Only mechanically provable integrity criteria use `passed`. Front matter,
semantic body structure, references, figures/tables/formulae, and non-body
classification remain `partial` until independent semantic review.

## Committed 17-page baseline

For `arxiv-2503-17744v1`, stage 5 changes the baseline as follows:

| Measure | Before | Stage 5 |
|---|---:|---:|
| Addressable elements | 397 | 167 |
| Structured authors | 0 | 25 |
| Structured affiliations | 0 | 6 |
| Structured abstracts | 0 | 1 |
| Structured publication dates | 0 | 1 |
| Body sections | 2 method subsections | 4 semantic sections |
| Body paragraphs | 388, including layout fragments | 31 |
| Structured references | 0 | 55, labels 1–55 consecutive |
| Structured figures with captions/resources | 0 | 3 |
| Explicit omissions | 0 | 97 |
| Page numbers left in body | 17 | 0 |
| Short layout fragments left as paragraphs | many | 0 |

The committed machine report is
`tests/golden/arxiv-2503-17744v1/quality.json`. Page 1 verifies the title,
25 authors, six affiliations, cross-page abstract, arXiv identifier, and date.
Pages 3, 6, and 8 verify normalized figure crops and captions. Pages 9–11 verify
Acknowledgments, Methods, M1/M2 hierarchy, and a detected table omission. Pages
12–17 verify 55 reference boundaries and order.

## Remaining limits

- Author-affiliation superscripts are represented as JATS `xref` links to six
  `aff` elements. The three dagger-marked authors link to one equal-contribution
  `author-notes/fn`. Provenance retains the full native multi-author line; a
  narrower per-name bbox is marked as approximate layout-derived revision data.
- The detected table has one explicit evidence-backed omission; cells are not
  fabricated from ambiguous glyph placement.
- Detected display-math regions have one evidence-backed omission each until
  a reliable TeX reconstruction engine is available. Inline mathematical text
  remains in its containing prose when it can be read safely. Formula-only
  fragments are not promoted to paragraphs, and prose on opposite sides of a
  display omission is not merged. Semantic formula recovery remains partial.
- Figure crops are produced only when a top-of-page visual region and caption
  boundary are reliable; the heuristic is not a general illustration detector.
- Independent visible-object page coverage is still not audited, so the package
  remains `valid: false` with only `page_coverage_not_audited`.
