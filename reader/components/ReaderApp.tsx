"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { buildToc, DocumentRenderer, documentTitle } from "./DocumentRenderer";
import {
  loadPackage,
  bootstrapSource,
  localDirectorySource,
  pageBootstrap,
  remoteSource,
  type ElementProvenance,
  type PackageLoadProgress,
  type PackageSource,
  type P2HPackage,
} from "../lib/p2h";

type Theme = "light" | "dark" | "system";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function loadingCopy(progress: PackageLoadProgress): { title: string; detail: string } {
  if (progress.phase === "manifest") return { title: "正在读取文档清单…", detail: "确定结构化数据的位置" };
  if (progress.phase === "discover") return { title: "正在读取注释索引…", detail: "确定需要加载的注释层" };
  if (progress.phase === "parse") return { title: "正在解析结构化文档…", detail: "检查 XML、溯源记录与注释" };
  if (progress.totalBytes !== undefined) {
    return {
      title: "正在下载结构化数据…",
      detail: `${formatBytes(progress.loadedBytes)} / ${formatBytes(progress.totalBytes)} · ${progress.completedFiles}/${progress.totalFiles} 个文件`,
    };
  }
  return {
    title: "正在下载结构化数据…",
    detail: `${formatBytes(progress.loadedBytes)} · ${progress.completedFiles}/${progress.totalFiles} 个文件`,
  };
}

function LoadingScreen({ progress }: { progress: PackageLoadProgress }) {
  const copy = loadingCopy(progress);
  const percent = progress.phase === "download" && progress.totalBytes !== undefined && progress.totalBytes > 0
    ? Math.min(100, (progress.loadedBytes / progress.totalBytes) * 100)
    : null;
  return (
    <main className="loading-screen">
      <div className="loader-mark">P2H</div>
      <div className="loading-copy">
        <p>{copy.title}</p>
        <span>{copy.detail}</span>
      </div>
      <div
        className={`loading-progress ${percent === null ? "indeterminate" : ""}`}
        role="progressbar"
        aria-label={copy.title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent === null ? undefined : Number(percent.toFixed(1))}
      >
        <i style={percent === null ? undefined : { width: `${percent}%` }} />
      </div>
      <div className="loading-measure">
        <span>{progress.phase === "parse" ? "解析阶段不估算百分比" : percent === null ? "正在确定总量" : "按实际读取字节计算"}</span>
        <strong>{percent === null ? "—" : `${percent.toFixed(1)}%`}</strong>
      </div>
    </main>
  );
}

function statusLabel(pkg: P2HPackage): { text: string; tone: string } {
  if (pkg.validation.valid) return { text: "验证通过", tone: "good" };
  if (Object.values(pkg.validation.checks).includes("failed")) return { text: "验证失败", tone: "bad" };
  return { text: "部分有效", tone: "warn" };
}

function EmptyState({ onLoadUrl, onPick }: { onLoadUrl: (url: string) => void; onPick: () => void }) {
  const [url, setUrl] = useState("");
  return (
    <main className="welcome-shell">
      <section className="welcome-copy">
        <p className="eyebrow">P2H PACKAGE 0.1 READER</p>
        <h1>让文献回到<br />适合阅读的形态。</h1>
        <p className="welcome-lead">Paper2HTML 阅读器直接呈现结构化 JATS/BITS 正文，同时保留译文、原稿定位与转换质量信息。</p>
      </section>
      <section className="open-card" aria-label="打开文档包">
        <div className="open-card-heading"><span>01</span><h2>打开文档包</h2></div>
        <button className="primary-action" onClick={onPick}>选择本地 P2H 目录 <span>→</span></button>
        <div className="or"><span>或者从已发布的包地址读取</span></div>
        <form onSubmit={(event) => { event.preventDefault(); if (url.trim()) onLoadUrl(url.trim()); }}>
          <label htmlFor="package-url">包根地址</label>
          <div className="url-row">
            <input id="package-url" type="url" placeholder="https://example.org/document/" value={url} onChange={(event) => setUrl(event.target.value)} />
            <button type="submit" aria-label="加载地址">载入</button>
          </div>
        </form>
        <p className="open-note">目录中应包含 <code>manifest.json</code>。所有文件只在当前浏览器中读取。</p>
      </section>
      <footer className="welcome-footer"><span>Paper2HTML project tool</span><span>JATS 1.3 · BITS 2.1</span></footer>
    </main>
  );
}

function EvidencePanel({ pkg, provenance, onClose }: { pkg: P2HPackage; provenance: ElementProvenance; onClose: () => void }) {
  const [sourceIndex, setSourceIndex] = useState(0);
  const source = provenance.sources[sourceIndex] ?? provenance.sources[0];
  if (!source) return null;
  const imageUrl = pkg.source.resourceUrl(source.page_image);
  return (
    <aside className="evidence-panel" aria-label="原稿证据">
      <header>
        <div><p>ORIGINAL EVIDENCE</p><h2>{provenance.element_id}</h2></div>
        <button onClick={onClose} aria-label="关闭原稿证据">×</button>
      </header>
      <div className="evidence-meta">
        <span>{source.source_id}</span><strong>第 {source.physical_page} 页</strong>
        <span>顺序 {provenance.reading_order}</span>
      </div>
      <div className="page-viewer">
        {/* Package evidence images can be local object URLs, so Next Image cannot optimize them. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {imageUrl ? <img src={imageUrl} alt={`原稿第 ${source.physical_page} 页`} /> : <div className="missing-page">页面图像未随当前选择载入</div>}
        {source.regions.map((region, index) => {
          const [x0, y0, x1, y1] = region.bbox;
          return <span key={index} className="evidence-box" style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }} />;
        })}
      </div>
      {provenance.sources.length > 1 && <div className="source-tabs">{provenance.sources.map((item, index) => <button className={sourceIndex === index ? "active" : ""} key={`${item.source_id}-${item.physical_page}`} onClick={() => setSourceIndex(index)}>页 {item.physical_page}</button>)}</div>}
      <p className="evidence-hint">高亮区域来自 provenance，不参与正文阅读顺序。</p>
    </aside>
  );
}

export default function ReaderApp() {
  const [pkg, setPkg] = useState<P2HPackage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeLayers, setActiveLayers] = useState<Set<string>>(new Set());
  const [fontSize, setFontSize] = useState(18);
  const [theme, setTheme] = useState<Theme>("system");
  const [progress, setProgress] = useState(0);
  const [loadProgress, setLoadProgress] = useState<PackageLoadProgress>({
    phase: "manifest", loadedBytes: 0, completedFiles: 0, totalFiles: 1,
  });
  const fileInput = useRef<HTMLInputElement>(null);
  const currentSource = useRef<PackageSource | null>(null);

  const openSource = async (source: PackageSource) => {
    setLoading(true); setError(""); setSelectedId(null);
    setLoadProgress({ phase: "manifest", loadedBytes: 0, completedFiles: 0, totalFiles: 1 });
    try {
      const loaded = await loadPackage(source, setLoadProgress);
      currentSource.current?.dispose?.();
      currentSource.current = source;
      setPkg(loaded);
      setActiveLayers(new Set(loaded.annotationLayers.filter((layer) => layer.kind === "translation").map((layer) => layer.id)));
      if (!pageBootstrap() && window.location.protocol !== "file:") {
        window.history.replaceState(null, "", source.label.startsWith("http") ? `?package=${encodeURIComponent(source.label)}` : window.location.pathname);
      }
      requestAnimationFrame(() => window.scrollTo({ top: Number(localStorage.getItem(`p2h-scroll:${loaded.manifest.package_id}`)) || 0 }));
    } catch (reason) {
      source.dispose?.();
      setError(reason instanceof Error ? reason.message : "无法打开文档包");
    } finally { setLoading(false); }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const savedTheme = (localStorage.getItem("p2h-theme") as Theme | null) ?? "system";
      const savedFont = Number(localStorage.getItem("p2h-font-size"));
      setTheme(savedTheme);
      if (savedFont >= 15 && savedFont <= 24) setFontSize(savedFont);
      try {
        const packageUrl = new URLSearchParams(window.location.search).get("package");
        const bootstrap = pageBootstrap();
        if (packageUrl) void openSource(remoteSource(packageUrl));
        else if (bootstrap) void openSource(bootstrapSource(bootstrap));
        else setLoading(false);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Reader 启动失败");
        setLoading(false);
      }
    }, 0);
    return () => { window.clearTimeout(timer); currentSource.current?.dispose?.(); };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("p2h-theme", theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.style.setProperty("--reader-size", `${fontSize}px`);
    localStorage.setItem("p2h-font-size", String(fontSize));
  }, [fontSize]);

  useEffect(() => {
    let timer = 0;
    const onScroll = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 0);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => pkg && localStorage.setItem(`p2h-scroll:${pkg.manifest.package_id}`, String(window.scrollY)), 160);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.removeEventListener("scroll", onScroll); window.clearTimeout(timer); };
  }, [pkg]);

  const toc = useMemo(() => pkg ? buildToc(pkg.xml) : [], [pkg]);
  const selected = selectedId && pkg ? pkg.provenance.get(selectedId) : undefined;
  const status = pkg ? statusLabel(pkg) : null;

  const chooseDirectory = () => fileInput.current?.click();
  const onFiles = (files: FileList | null) => { if (files?.length) { try { void openSource(localDirectorySource(files)); } catch (reason) { setError(reason instanceof Error ? reason.message : "目录无法读取"); } } };

  const directoryRef = (node: HTMLInputElement | null) => { fileInput.current = node; node?.setAttribute("webkitdirectory", ""); };

  const navigateFromToc = async (event: ReactMouseEvent<HTMLAnchorElement>, id: string) => {
    event.preventDefault();
    setDrawerOpen(false);
    window.history.pushState(null, "", `#${encodeURIComponent(id)}`);
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    const target = document.getElementById(id);
    if (!target) return;
    const precedingImages = [...document.images].filter((image) =>
      Boolean(target.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_PRECEDING),
    );
    for (const image of precedingImages) image.loading = "eager";
    await Promise.allSettled(
      precedingImages.filter((image) => !image.complete).map((image) => image.decode()),
    );
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (!pkg && !loading) return <><input ref={directoryRef} className="visually-hidden" type="file" multiple onChange={(event) => onFiles(event.target.files)} /><EmptyState onPick={chooseDirectory} onLoadUrl={(url) => void openSource(remoteSource(url))} />{error && <div className="toast error-toast" role="alert"><strong>打开失败</strong>{error}<button onClick={() => setError("")}>×</button></div>}</>;

  if (loading && !pkg) return <LoadingScreen progress={loadProgress} />;
  if (!pkg) return null;

  const title = documentTitle(pkg.xml);
  return (
    <div className={`reader-shell ${selected ? "with-evidence" : ""}`}>
      <div className="reading-progress" style={{ width: `${progress}%` }} />
      <header className="reader-toolbar">
        <button className="toolbar-icon" onClick={() => setDrawerOpen(true)} aria-label="打开目录">☰</button>
        <a className="reader-brand" href={window.location.pathname} aria-label="返回打开页"><strong>P2H</strong><span>{title}</span></a>
        <div className="toolbar-actions">
          <button onClick={() => setFontSize((size) => Math.max(15, size - 1))} aria-label="减小字号">A−</button>
          <button onClick={() => setFontSize((size) => Math.min(24, size + 1))} aria-label="增大字号">A+</button>
          <button onClick={() => setTheme((value) => value === "system" ? "light" : value === "light" ? "dark" : "system")} aria-label="切换主题">{theme === "dark" ? "☾" : theme === "light" ? "☀" : "◐"}</button>
          <button className={`validation-pill ${status?.tone}`} onClick={() => setInfoOpen(!infoOpen)}><i />{status?.text}</button>
          <button className="open-another" onClick={chooseDirectory}>打开</button>
        </div>
      </header>
      <input ref={directoryRef} className="visually-hidden" type="file" multiple onChange={(event) => onFiles(event.target.files)} />

      <aside className={`toc-panel ${drawerOpen ? "open" : ""}`}>
        <header><p>CONTENTS</p><button onClick={() => setDrawerOpen(false)} aria-label="关闭目录">×</button></header>
        <nav>{toc.map((item) => <a key={item.id} className={`toc-level-${item.level}`} href={`#${item.id}`} onClick={(event) => void navigateFromToc(event, item.id)}>{item.label}</a>)}</nav>
        {pkg.annotationLayers.length > 0 && <section className="layer-controls"><p>ANNOTATION LAYERS</p>{pkg.annotationLayers.map((layer) => <label key={layer.id}><input type="checkbox" checked={activeLayers.has(layer.id)} onChange={() => setActiveLayers((current) => { const next = new Set(current); if (next.has(layer.id)) next.delete(layer.id); else next.add(layer.id); return next; })} /><span>{layer.kind === "translation" ? `译文 · ${layer.language}` : `${layer.kind} · ${layer.language}`}</span></label>)}</section>}
      </aside>
      {drawerOpen && <button className="drawer-scrim" onClick={() => setDrawerOpen(false)} aria-label="关闭目录" />}

      <main className="reader-main">
        {infoOpen && <>
          <button className="validation-scrim" onClick={() => setInfoOpen(false)} aria-label="关闭验证详情" />
          <section className={`validation-card ${status?.tone}`} role="dialog" aria-modal="true" aria-label="文档验证状态">
            <header><div><p>PACKAGE STATUS</p><h2>{status?.text}</h2></div><button onClick={() => setInfoOpen(false)} aria-label="关闭验证详情">×</button></header>
            <p>{pkg.validation.valid ? "该目录通过 P2H Package 0.1 的全部强制检查。" : "该文档仍可供审阅，但不能声明为完全合规的 P2H Package。"}</p>
            <div className="check-grid">{Object.entries(pkg.validation.checks).map(([name, value]) => <span key={name} className={value}><i />{name.replaceAll("_", " ")}</span>)}</div>
            {[...pkg.validation.errors, ...pkg.validation.warnings].map((item) => <div className="validation-message" key={item.code}><code>{item.code}</code>{item.message}</div>)}
          </section>
        </>}
        <article className="reading-paper">
          <DocumentRenderer pkg={pkg} activeLayers={activeLayers} onSelect={setSelectedId} />
        </article>
        <footer className="document-end"><span>END OF DOCUMENT</span><p>{pkg.manifest.package_id}</p></footer>
      </main>
      {selected && <EvidencePanel key={selected.element_id} pkg={pkg} provenance={selected} onClose={() => setSelectedId(null)} />}
      {error && <div className="toast error-toast" role="alert"><strong>载入失败</strong>{error}<button onClick={() => setError("")}>×</button></div>}
    </div>
  );
}
