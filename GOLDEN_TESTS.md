# Golden conversion regression

The committed golden case is `tests/golden/arxiv-2503-17744v1/`. It contains a
version-pinned arXiv PDF descriptor and the conversion's structured expected
files. It deliberately contains neither the PDF nor rendered page PNGs.

The `expected/` directory and `projection.json` are regression evidence, not a
complete P2H Package. They must never be described as conforming or passed to
the package validator on their own: P2H Package 0.1 requires every page PNG.

`quality.json` is the machine-readable semantic quality checklist for the same
run. It deliberately reports `partial` while tables, formulae, and independent
page coverage remain incomplete. Projection comparison includes this report and
normalized figure-resource hashes.

## Fast offline regression

Once the repository's Python development environment is installed, a fresh
clone can run the golden and converter fast tests below without test-time
network access. They exercise descriptor parsing and verified-cache behavior
with local bytes, converter determinism, golden projection comparison, and
accidental-update protection:

```sh
uv run pytest -q tests/test_converter.py tests/test_golden.py
```

The repository's entire `pytest -q` suite is not an offline command on a fresh
machine. Validator normative-engine tests need the locked JATS/BITS, W3C, and
SchXslt resources, and some tests intentionally populate a fresh temporary
cache. The validator CLI itself can run offline after its selected persistent
cache has been prewarmed, but that does not make the full test suite offline.

## Real 17-page network regression

On macOS with Poppler and Apple Vision prerequisites, run:

```sh
P2H_RUN_NETWORK_GOLDEN=1 uv run pytest -q -m integration \
  tests/test_converter_integration.py
```

The integration test enables the converter's pinned-TLS secure DNS mode so it
also works on hosts whose local proxy publishes reserved synthetic DNS
addresses. It never connects to those synthetic addresses.

The test downloads the exact descriptor bytes into a temporary verified cache,
converts all 17 pages, independently validates the complete generated package,
and compares it with `projection.json`. It then unconditionally converts the
downloaded cache file as a local PDF and checks that local and URL inputs
produce identical JATS, pages, elements, and omissions files. No ignored
`testdata/` file is required.

The stable projection checks exact JATS, native text/coordinates, reading order,
metadata provenance, source identity, page dimensions/count, omission state,
and validator statuses/codes. It ignores run timestamps and OCR engine version,
confidence, and exact OCR wording. Page pixels use exact decoded-RGB hashes when
the Poppler version matches the baseline; otherwise a small perceptual-hash
tolerance avoids treating harmless renderer-version changes as a regression.

## Deliberate baseline update

Generate and inspect a complete package first. Updating requires both the case
ID and an exact confirmation token, so an ordinary check cannot rewrite the
baseline:

```sh
uv run python -m src.converter.golden GENERATED_PACKAGE \
  tests/golden/arxiv-2503-17744v1 \
  --update \
  --case-id arxiv-2503-17744v1 \
  --confirm-update arxiv-2503-17744v1
```

Review the structured diff and page-render summaries before accepting an
update. Never copy the generated `assets/evidence/pages/` directory into Git.
