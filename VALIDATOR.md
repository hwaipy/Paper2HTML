# P2H Package 0.1 validator

`src.validator.cli` is an independent, offline-capable validator for the package
defined by `STRUCTURED_DOCUMENT_PACKAGE_SPEC.md`. It does not trust an existing
`validation/report.json`: that file is schema-checked, while the result printed
by the command is computed again from the package.

## Install

Python 3.11 or newer is required.

```sh
uv sync --extra dev
```

This repository tool is not a Python installation package, does not build a
wheel, and does not install a standalone command. Its source remains under
`src/validator/` and is run as a Python module through the repository's `uv`
environment.

The validator uses the repository's JSON Schemas and P2H Schematron directly.
Official JATS 1.3, BITS 2.1, and W3C XML schemas are downloaded only when
`--allow-network` is supplied. Every download is checked against
`schema/0.1/upstream-lock.json` before use. SchXslt 1.10.1 is likewise pinned by
SHA-256 in the implementation and compiles the XSLT 2 Schematron for SaxonC.
After one online run, omit `--allow-network` to validate entirely from cache.

## Command line

```sh
uv run python -m src.validator.cli PACKAGE --allow-network
uv run python -m src.validator.cli PACKAGE --json
uv run python -m src.validator.cli PACKAGE --write-report
uv run python -m src.validator.cli PACKAGE --coverage-evidence independent-audit.json
uv run python -m src.validator.cli PACKAGE --katex-command 'your-katex-check-command'
```

Validation is read-only by default. `--write-report` atomically replaces
`validation/report.json`, then updates only that file's entry in
`checksums.sha256`. This avoids checksum self-reference: the checksum list
excludes itself, while the generated report is included after its final bytes
are known. All other checksum entries are preserved and independently checked.

Exit codes are stable:

- `0`: conforming package (`valid: true`);
- `1`: validation completed and the package is not conforming;
- `2`: invalid invocation or a validator/resource execution failure.

## Page coverage

Filesystem and provenance checks cannot prove that no visible page object was
missed. Without independent evidence, `page_coverage` is `partial` and the
package is not conforming. This is intentional.

An external OCR/layout auditor can provide JSON with this interface:

```json
{
  "engine": "example-layout-auditor",
  "engine_version": "1.0.0",
  "reviewed_pages": [
    {"source_id": "src-001", "physical_page": 1}
  ],
  "uncovered_objects": []
}
```

`reviewed_pages` must equal the package's complete page set. Each uncovered
object produces an error. The auditor identity is copied to the generated
report as `x-page-coverage-auditor`. This narrow interface is designed for a
future built-in visual auditor without coupling OCR or a reader to the core
validator.

When formula elements are present, `--katex-command` must name an executable
that reads one delimiter-free TeX expression from standard input and exits
nonzero for invalid input. Without it, formula validation fails explicitly;
the validator never substitutes a looser parser or silently skips formulas.

## Validation layers

The implementation executes all ten report checks and the cross-file rules in
specification sections 21 and 25: safe paths and text encoding; JSON Schema;
locked JATS/BITS XSD; P2H Schematron full phase; IDs, links, annotations and XML
metadata; page continuity and PNG geometry; provenance, candidates, regions and
reading order; resources, MIME and table spans; independent coverage evidence;
and exhaustive SHA-256 verification.

If a locked XSD, SaxonC, SchXslt, or another mandatory validation engine cannot
run, its check is failed and the command returns exit code 2. Such a layer is
never silently reported as passed.
