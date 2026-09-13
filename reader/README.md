# Paper2HTML Reader

Paper2HTML Reader is the project-level web reader for P2H Package 0.1. It is
maintained once in this repository and is not copied into converted packages.
The agreed delivery design uses a hosted, immutable Reader release from both
`index.html` and `index-local.html`; see
[`READER_DELIVERY.md`](../READER_DELIVERY.md). This delivery
path is designed independently of Sites.

## Run locally

```sh
cd reader
npm install
npm run dev
```

Open the displayed local address. During the current test phase, the reader
starts at the package-opening screen. Use **Open** to select a complete local
P2H package directory; the browser reads the selected files without uploading
them. A hosted package can also be supplied through the `package` query
parameter; cross-origin servers must allow browser requests.

For repeated local visual testing, copy or generate a complete package under
`public/golden/`, then open `/?package=/golden/`. That directory is ignored by
Git because it contains generated page evidence and content resources; it is
not Reader source or the committed structure-only golden projection.

## Supported in this first version

- P2H Package 0.1 manifest and validation status;
- JATS 1.3 articles and the common BITS 2.1 book structure;
- headings, paragraphs, lists, figures, tables, references, notes, code and
  KaTeX-compatible formulae;
- stable-ID cross references and XML document-order rendering;
- optional translation and other annotation layers;
- provenance page images and normalized region overlays;
- responsive layout, contents navigation, themes, type scaling, print styles,
  reading progress and position restoration.

Unknown XML elements retain their textual descendants. Package data is rendered
through an explicit element mapping; untrusted package markup is not inserted as
arbitrary HTML. XML resource paths are limited to the P2H `assets/content/`
location.

## Package sources

The Reader preserves one loading and rendering pipeline behind a shared
`PackageSource` boundary:

- `index.html` selects an HTTP source whose text reads use same-origin `fetch()`;
- `index-local.html` selects an embedded source containing the package's Reader-
  relevant UTF-8 text snapshot;
- both modes resolve images and other binary assets as paths relative to the
  entry HTML, without Blob URLs or embedded binary data.

## Build the hosted Reader release

```sh
npm run build:release
```

The standalone Vite release build writes `release/0.1.2/reader.js` and
`reader.css`, plus external KaTeX WOFF2 files under `release/0.1.2/fonts/`. Publish
the complete version directory at the HTTPS base URL selected by the
Converter. This build is independent of the Vinext development application and
does not require Sites.

Published versions are registered in [`releases.json`](releases.json). The
Converter reads its default version, base URL, and entrypoint names from this
machine-readable catalog. A released version directory is immutable: changes
must use a new version and a new catalog entry rather than replacing old files.

## Checks

```sh
npm run lint
npx tsc --noEmit --incremental false
npm test
```
