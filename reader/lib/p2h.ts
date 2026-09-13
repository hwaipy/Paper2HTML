export type Manifest = {
  format: string;
  format_version: string;
  package_id: string;
  document: {
    id: string;
    type: "article" | "book" | "thesis" | "report" | "other";
    profile: "jats-1.3" | "bits-2.1";
    language: string;
    content: string;
  };
  sources: Array<{ id: string; role: string; original_name: string; page_count: number }>;
  provenance: { pages: string; elements: string; omissions: string };
  annotations?: { index: string };
  validation: string;
};

export type ValidationReport = {
  valid: boolean;
  checks: Record<string, "passed" | "failed" | "partial" | "not-run" | "not-applicable">;
  statistics?: Record<string, number>;
  errors: Array<{ code: string; message: string }>;
  warnings: Array<{ code: string; message: string }>;
};

export type Region = { bbox: [number, number, number, number]; polygon?: number[][] };
export type ElementSource = {
  source_id: string;
  physical_page: number;
  logical_page_id: string;
  page_image: string;
  regions: Region[];
};
export type ElementProvenance = {
  element_id: string;
  reading_order: number;
  sources: ElementSource[];
  decision?: { method: string; confidence: number };
};
export type Annotation = {
  target_id: string;
  kind: "translation" | "definition" | "commentary" | "reading-note";
  language: string;
  content_text?: string;
  content_xml?: string;
};
export type AnnotationLayer = {
  id: string;
  kind: Annotation["kind"];
  language: string;
  path: string;
  records: Annotation[];
};

export interface PackageSource {
  label: string;
  readText(path: string, onProgress?: (progress: TextReadProgress) => void): Promise<string>;
  resourceUrl(path: string): string;
  dispose?(): void;
}

export type TextReadProgress = {
  path: string;
  loadedBytes: number;
  totalBytes?: number;
};

export type PackageLoadProgress = {
  phase: "manifest" | "discover" | "download" | "parse";
  loadedBytes: number;
  totalBytes?: number;
  completedFiles: number;
  totalFiles: number;
};

export type ReaderBootstrap = {
  protocol: "paper2html-reader-bootstrap/1";
  mode: "http" | "embedded";
  packageBase: string;
  files?: Record<string, string>;
};

declare global {
  interface Window {
    PAPER2HTML_BOOTSTRAP?: ReaderBootstrap;
  }
}

export type P2HPackage = {
  source: PackageSource;
  manifest: Manifest;
  validation: ValidationReport;
  xml: XMLDocument;
  provenance: Map<string, ElementProvenance>;
  annotationLayers: AnnotationLayer[];
};

const SAFE_PATH = /^(?!\/)(?!.*(?:^|\/)\.\.(?:\/|$))[A-Za-z0-9._/-]+$/;

function responseLength(response: Response): number | undefined {
  if (response.headers.get("content-encoding")) return undefined;
  const value = Number(response.headers.get("content-length"));
  return Number.isFinite(value) && value >= 0 ? value : undefined;
}

async function readUtf8Stream(
  stream: ReadableStream<Uint8Array>,
  path: string,
  totalBytes: number | undefined,
  onProgress?: (progress: TextReadProgress) => void,
): Promise<string> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const chunks: string[] = [];
  let loadedBytes = 0;
  onProgress?.({ path, loadedBytes, totalBytes });
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      loadedBytes += value.byteLength;
      chunks.push(decoder.decode(value, { stream: true }));
      onProgress?.({
        path,
        loadedBytes,
        totalBytes: totalBytes !== undefined && loadedBytes <= totalBytes ? totalBytes : undefined,
      });
    }
    chunks.push(decoder.decode());
  } catch {
    throw new Error(`${path} 不是有效的 UTF-8 文本`);
  }
  onProgress?.({
    path,
    loadedBytes,
    totalBytes: totalBytes !== undefined && loadedBytes === totalBytes ? totalBytes : undefined,
  });
  return chunks.join("");
}

export function assertPackagePath(path: string): string {
  const normalized = path.replaceAll("\\", "/");
  if (!SAFE_PATH.test(normalized) || normalized.includes("//")) {
    throw new Error(`包内路径不安全：${path}`);
  }
  return normalized;
}

export function xmlResourcePath(href: string): string {
  if (!href.startsWith("../assets/content/")) {
    throw new Error(`XML 资源路径超出 assets/content：${href}`);
  }
  return assertPackagePath(href.slice(3));
}

export function remoteSource(baseInput: string): PackageSource {
  const base = new URL(baseInput, window.location.href);
  if (!base.pathname.endsWith("/")) base.pathname += "/";
  const root = base.href;
  const resolve = (path: string) => new URL(assertPackagePath(path), root).href;
  return {
    label: root,
    async readText(path, onProgress) {
      const response = await fetch(resolve(path), { cache: "no-cache" });
      if (!response.ok) throw new Error(`${path} 加载失败（HTTP ${response.status}）`);
      if (response.body) return readUtf8Stream(response.body, path, responseLength(response), onProgress);
      const text = await response.text();
      const loadedBytes = new TextEncoder().encode(text).byteLength;
      onProgress?.({ path, loadedBytes, totalBytes: loadedBytes });
      return text;
    },
    resourceUrl: resolve,
  };
}

function packageRoot(baseInput: string): URL {
  const base = new URL(baseInput, window.location.href);
  if (!base.pathname.endsWith("/")) base.pathname += "/";
  return base;
}

function decodeUtf8Base64(value: string, path: string): string {
  try {
    const bytes = Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error(`本地文本快照无法解码：${path}`);
  }
}

export function bootstrapSource(bootstrap: ReaderBootstrap): PackageSource {
  if (bootstrap.protocol !== "paper2html-reader-bootstrap/1") {
    throw new Error(`不支持的 Reader 启动协议：${bootstrap.protocol}`);
  }
  const root = packageRoot(bootstrap.packageBase);
  const resolve = (path: string) => new URL(assertPackagePath(path), root).href;
  if (bootstrap.mode === "http") return remoteSource(root.href);
  if (bootstrap.mode !== "embedded" || !bootstrap.files) {
    throw new Error("本地 Reader 启动数据不完整");
  }
  return {
    label: "本地 P2H 文档包",
    async readText(path, onProgress) {
      const safe = assertPackagePath(path);
      const encoded = bootstrap.files?.[safe];
      if (encoded === undefined) throw new Error(`本地文本快照缺少文件：${safe}`);
      const text = decodeUtf8Base64(encoded, safe);
      const loadedBytes = new TextEncoder().encode(text).byteLength;
      onProgress?.({ path: safe, loadedBytes, totalBytes: loadedBytes });
      return text;
    },
    resourceUrl: resolve,
  };
}

export function pageBootstrap(): ReaderBootstrap | undefined {
  if (window.PAPER2HTML_BOOTSTRAP) return window.PAPER2HTML_BOOTSTRAP;
  const element = document.getElementById("paper2html-bootstrap");
  if (!element?.textContent) return undefined;
  try {
    const value = JSON.parse(element.textContent) as ReaderBootstrap;
    window.PAPER2HTML_BOOTSTRAP = value;
    return value;
  } catch {
    throw new Error("Reader 启动配置不是有效 JSON");
  }
}

export function localDirectorySource(files: FileList): PackageSource {
  const all = [...files];
  const manifestFile = all.find((file) => file.webkitRelativePath.endsWith("/manifest.json") || file.name === "manifest.json");
  if (!manifestFile) throw new Error("所选目录中没有 manifest.json");
  const manifestRelative = manifestFile.webkitRelativePath || manifestFile.name;
  const prefix = manifestRelative.slice(0, -"manifest.json".length);
  const index = new Map<string, File>();
  for (const file of all) {
    const relative = (file.webkitRelativePath || file.name).slice(prefix.length);
    if (relative) index.set(relative, file);
  }
  const objectUrls = new Map<string, string>();
  return {
    label: manifestRelative.slice(0, -1),
    async readText(path, onProgress) {
      const safe = assertPackagePath(path);
      const file = index.get(safe);
      if (!file) throw new Error(`包内缺少文件：${safe}`);
      return readUtf8Stream(file.stream(), safe, file.size, onProgress);
    },
    resourceUrl(path) {
      const safe = assertPackagePath(path);
      const file = index.get(safe);
      if (!file) return "";
      if (!objectUrls.has(safe)) objectUrls.set(safe, URL.createObjectURL(file));
      return objectUrls.get(safe)!;
    },
    dispose() {
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    },
  };
}

function parseJsonl<T>(raw: string): T[] {
  return raw
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line, index) => {
      try {
        return JSON.parse(line) as T;
      } catch {
        throw new Error(`JSONL 第 ${index + 1} 行无法解析`);
      }
    });
}

function parseXml(raw: string): XMLDocument {
  const xml = new DOMParser().parseFromString(raw, "application/xml");
  const error = xml.querySelector("parsererror");
  if (error) throw new Error(`document.xml 无法解析：${error.textContent?.trim()}`);
  return xml;
}

export async function loadPackage(
  source: PackageSource,
  onProgress?: (progress: PackageLoadProgress) => void,
): Promise<P2HPackage> {
  onProgress?.({ phase: "manifest", loadedBytes: 0, completedFiles: 0, totalFiles: 1 });
  const manifest = JSON.parse(await source.readText("manifest.json", (progress) => {
    onProgress?.({ phase: "manifest", loadedBytes: progress.loadedBytes, completedFiles: 0, totalFiles: 1 });
  })) as Manifest;
  if (manifest.format !== "paper2html-package" || manifest.format_version !== "0.1") {
    throw new Error(`不支持的包格式：${manifest.format ?? "unknown"} ${manifest.format_version ?? ""}`);
  }
  let annotationIndex: { layers: Omit<AnnotationLayer, "records">[] } | undefined;
  if (manifest.annotations) {
    onProgress?.({ phase: "discover", loadedBytes: 0, completedFiles: 0, totalFiles: 1 });
    annotationIndex = JSON.parse(
      await source.readText(assertPackagePath(manifest.annotations.index), (progress) => {
        onProgress?.({ phase: "discover", loadedBytes: progress.loadedBytes, completedFiles: 0, totalFiles: 1 });
      }),
    ) as { layers: Omit<AnnotationLayer, "records">[] };
  }

  const contentPath = assertPackagePath(manifest.document.content);
  const validationPath = assertPackagePath(manifest.validation);
  const elementsPath = assertPackagePath(manifest.provenance.elements);
  const requiredPaths = [
    contentPath,
    validationPath,
    elementsPath,
    ...(annotationIndex?.layers.map((layer) => assertPackagePath(layer.path)) ?? []),
  ];
  const fileProgress = new Map(requiredPaths.map((path) => [path, { loadedBytes: 0, totalBytes: undefined as number | undefined }]));
  let completedFiles = 0;
  const reportDownload = () => {
    const values = [...fileProgress.values()];
    const loadedBytes = values.reduce((sum, item) => sum + item.loadedBytes, 0);
    const allTotalsKnown = values.every((item) => item.totalBytes !== undefined);
    onProgress?.({
      phase: "download",
      loadedBytes,
      totalBytes: allTotalsKnown ? values.reduce((sum, item) => sum + item.totalBytes!, 0) : undefined,
      completedFiles,
      totalFiles: requiredPaths.length,
    });
  };
  reportDownload();
  const entries = await Promise.all(requiredPaths.map(async (path) => {
    const raw = await source.readText(path, (progress) => {
      fileProgress.set(path, { loadedBytes: progress.loadedBytes, totalBytes: progress.totalBytes });
      reportDownload();
    });
    completedFiles += 1;
    reportDownload();
    return [path, raw] as const;
  }));
  const rawByPath = new Map(entries);
  onProgress?.({
    phase: "parse",
    loadedBytes: [...fileProgress.values()].reduce((sum, item) => sum + item.loadedBytes, 0),
    completedFiles,
    totalFiles: requiredPaths.length,
  });
  const xmlRaw = rawByPath.get(contentPath)!;
  const validationRaw = rawByPath.get(validationPath)!;
  const elementsRaw = rawByPath.get(elementsPath)!;
  const provenance = new Map(
    parseJsonl<ElementProvenance>(elementsRaw).map((record) => [record.element_id, record]),
  );
  const annotationLayers: AnnotationLayer[] = [];
  if (annotationIndex) {
    for (const layer of annotationIndex.layers) {
      const raw = rawByPath.get(assertPackagePath(layer.path))!;
      annotationLayers.push({ ...layer, records: parseJsonl<Annotation>(raw) });
    }
  }
  return {
    source,
    manifest,
    validation: JSON.parse(validationRaw) as ValidationReport,
    xml: parseXml(xmlRaw),
    provenance,
    annotationLayers,
  };
}
