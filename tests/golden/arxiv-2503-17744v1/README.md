# arxiv-2503-17744v1 golden projection

This directory pins the public arXiv v1 download by byte size and SHA-256. It
does not redistribute the PDF.

`expected/` contains only structured conversion output. `projection.json`
contains regression digests and page-render summaries. Neither directory is a
complete P2H Package because the required page PNGs are intentionally absent.

`quality.json` records the machine-checkable stage-5 semantic baseline and its
explicit remaining limitations.

See `GOLDEN_TESTS.md` for the comparison and guarded update commands.
