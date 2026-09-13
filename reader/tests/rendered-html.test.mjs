import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { access, readdir, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Paper2HTML reader entry page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Paper2HTML Reader<\/title>/i);
  assert.match(html, /正在读取文档清单/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/i);
});

test("does not require a generated package in Reader source", async () => {
  const source = await readFile(new URL("../components/ReaderApp.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /DEFAULT_GOLDEN_PACKAGE|openSource\(remoteSource\("\/golden\/"\)\)/);
  assert.match(source, /else setLoading\(false\)/);
});

test("keeps late table-of-contents targets stable while images load", async () => {
  const source = await readFile(new URL("../components/ReaderApp.tsx", import.meta.url), "utf8");
  assert.match(source, /image\.loading = "eager"/);
  assert.match(source, /Promise\.allSettled/);
  assert.match(source, /target\.scrollIntoView/);
});

test("shows validation details as a viewport modal", async () => {
  const source = await readFile(new URL("../components/ReaderApp.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(source, /role="dialog" aria-modal="true"/);
  assert.match(source, /className="validation-scrim"/);
  assert.match(css, /\.validation-card \{ position: fixed;/);
});

test("builds the independently hosted Reader release", async () => {
  const catalog = JSON.parse(await readFile(new URL("../releases.json", import.meta.url), "utf8"));
  const releaseDirectory = new URL(`../release/${catalog.default}/`, import.meta.url);
  await Promise.all([
    access(new URL("reader.js", releaseDirectory)),
    access(new URL("reader.css", releaseDirectory)),
  ]);
  const css = await readFile(new URL("reader.css", releaseDirectory), "utf8");
  assert.match(css, /url\(\.\/fonts\/KaTeX_Main-Regular\.woff2\)/);
  assert.doesNotMatch(css, /data:font|url\(\/fonts\/|\.woff\)|\.ttf\)/);
  const fonts = await readdir(new URL("fonts/", releaseDirectory));
  assert.ok(fonts.length > 0);
  assert.ok(fonts.every((name) => name.endsWith(".woff2")));
});

test("keeps the release catalog synchronized with the build", async () => {
  const catalog = JSON.parse(await readFile(new URL("../releases.json", import.meta.url), "utf8"));
  assert.equal(catalog.format, "paper2html-reader-releases");
  assert.equal(catalog.format_version, "1");
  const release = catalog.releases[catalog.default];
  assert.ok(release.base_url.endsWith(`/reader/${catalog.default}/`));
  for (const name of [release.entrypoints.stylesheet, release.entrypoints.script]) {
    const bytes = await readFile(new URL(`../release/${catalog.default}/${name}`, import.meta.url));
    assert.equal(createHash("sha256").update(bytes).digest("hex"), release.sha256[name]);
  }
});
