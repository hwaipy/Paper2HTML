"use client";

import katex from "katex";
import type { ReactNode } from "react";
import type { Annotation, P2HPackage } from "../lib/p2h";
import { xmlResourcePath } from "../lib/p2h";

type Props = {
  pkg: P2HPackage;
  activeLayers: Set<string>;
  onSelect: (id: string) => void;
};

const XLINK = "http://www.w3.org/1999/xlink";
const BLOCK_NAMES = new Set([
  "article-title", "book-title", "subtitle", "title", "p", "sec", "book-part", "abstract",
  "fig", "caption", "table-wrap", "list", "list-item", "disp-formula", "ref-list", "ref",
  "fn", "preformat", "code", "boxed-text", "aff",
]);

function directChildren(node: Element, excluded: string[] = []): Node[] {
  return [...node.childNodes].filter((child) => child.nodeType !== Node.ELEMENT_NODE || !excluded.includes((child as Element).localName));
}

function directText(node: Element, selector: string): string {
  return node.querySelector(selector)?.textContent?.trim() || "";
}

export function documentTitle(xml: XMLDocument): string {
  return directText(xml.documentElement, "article-title, book-title") || "Untitled document";
}

export function buildToc(xml: XMLDocument): Array<{ id: string; label: string; level: number }> {
  const result: Array<{ id: string; label: string; level: number }> = [];
  xml.querySelectorAll("article-title[id], book-title[id], sec > title[id], book-part > book-part-meta title[id]").forEach((title) => {
    let level = 1;
    let cursor = title.parentElement;
    while (cursor) {
      if (cursor.localName === "sec" || cursor.localName === "book-part") level += 1;
      cursor = cursor.parentElement;
    }
    result.push({ id: title.id, label: title.textContent?.trim() || title.id, level: Math.min(level, 4) });
  });
  return result;
}

function AnnotationView({ annotation, renderFragment }: { annotation: Annotation; renderFragment: (xml: string) => ReactNode }) {
  return (
    <aside className={`annotation annotation-${annotation.kind}`} lang={annotation.language}>
      <span className="annotation-label">{annotation.kind === "translation" ? "译文" : annotation.kind}</span>
      {annotation.content_text ?? (annotation.content_xml ? renderFragment(annotation.content_xml) : null)}
    </aside>
  );
}

export function DocumentRenderer({ pkg, activeLayers, onSelect }: Props) {
  const annotations = new Map<string, Annotation[]>();
  for (const layer of pkg.annotationLayers) {
    if (!activeLayers.has(layer.id)) continue;
    for (const record of layer.records) {
      const current = annotations.get(record.target_id) ?? [];
      current.push(record);
      annotations.set(record.target_id, current);
    }
  }

  const renderChildren = (node: Node, excluded: string[] = []): ReactNode[] =>
    (node.nodeType === Node.ELEMENT_NODE ? directChildren(node as Element, excluded) : [...node.childNodes]).map((child, index) => renderNode(child, index));

  const renderFragment = (raw: string): ReactNode => {
    const fragment = new DOMParser().parseFromString(`<p2h-fragment>${raw}</p2h-fragment>`, "application/xml");
    if (fragment.querySelector("parsererror")) return raw;
    return renderChildren(fragment.documentElement);
  };

  const addressable = (element: Element, body: ReactNode, className = ""): ReactNode => {
    const id = element.id;
    const notes = id ? annotations.get(id) : undefined;
    const clickable = Boolean(id && pkg.provenance.has(id));
    const Wrapper = BLOCK_NAMES.has(element.localName) ? "div" : "span";
    return (
      <Wrapper
        id={id || undefined}
        className={`${className} ${clickable ? "addressable" : ""}`.trim()}
        data-p2h-id={id || undefined}
        onClick={clickable ? (event) => { event.stopPropagation(); onSelect(id); } : undefined}
      >
        {body}
        {notes?.map((note, index) => <AnnotationView key={`${id}-note-${index}`} annotation={note} renderFragment={renderFragment} />)}
      </Wrapper>
    );
  };

  const renderNode = (node: Node, key: number | string): ReactNode => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const element = node as Element;
    const name = element.localName;
    const children = () => renderChildren(element);
    const content = children();

    switch (name) {
      case "article":
      case "book": return <div key={key} className={`p2h-document p2h-${name}`} lang={element.getAttribute("xml:lang") ?? undefined}>{content}</div>;
      case "front":
      case "book-meta": return <header key={key} className="document-front">{content}</header>;
      case "article-meta":
      case "book-part-meta": return <div key={key} className="document-meta">{content}</div>;
      case "journal-meta": return <div key={key} className="journal-meta">{content}</div>;
      case "journal-title-group":
      case "title-group":
      case "contrib-group":
      case "book-body":
      case "body":
      case "back":
      case "book-back":
      case "fn-group": return <div key={key} className={name}>{content}</div>;
      case "article-title":
      case "book-title": return <div key={key}>{addressable(element, <h1>{content}</h1>, "document-title")}</div>;
      case "subtitle": return <div key={key}>{addressable(element, <p className="subtitle">{content}</p>)}</div>;
      case "sec":
      case "book-part": return <section key={key} className={name}>{content}</section>;
      case "title": {
        let depth = 2;
        let parent = element.parentElement;
        while (parent) { if (parent.localName === "sec" || parent.localName === "book-part") depth += 1; parent = parent.parentElement; }
        const Heading = `h${Math.min(depth, 6)}` as "h2";
        return <div key={key}>{addressable(element, <Heading>{content}</Heading>)}</div>;
      }
      case "abstract": return <section key={key} className="abstract">{element.querySelector(":scope > title") ? null : <h2>Abstract</h2>}{content}</section>;
      case "contrib": return <span key={key}>{addressable(element, <span className="contributor">{content}</span>)}</span>;
      case "name": {
        const given = directText(element, ":scope > given-names");
        const surname = directText(element, ":scope > surname");
        return <span key={key}>{addressable(element, <>{[given, surname].filter(Boolean).join(" ")}</>)}</span>;
      }
      case "given-names":
      case "surname": return null;
      case "aff": return <div key={key}>{addressable(element, <div className="affiliation">{content}</div>)}</div>;
      case "p": return <div key={key}>{addressable(element, <p>{content}</p>)}</div>;
      case "italic": return <em key={key}>{content}</em>;
      case "bold": return <strong key={key}>{content}</strong>;
      case "sup": return <sup key={key}>{content}</sup>;
      case "sub": return <sub key={key}>{content}</sub>;
      case "list": {
        const List = element.getAttribute("list-type") === "order" ? "ol" : "ul";
        return <div key={key}>{addressable(element, <List>{content}</List>)}</div>;
      }
      case "list-item": return <li key={key}>{addressable(element, content)}</li>;
      case "fig": return <div key={key}>{addressable(element, <figure>{content}</figure>)}</div>;
      case "caption": return <figcaption key={key}>{addressable(element, content)}</figcaption>;
      case "graphic": {
        const href = element.getAttributeNS(XLINK, "href") || element.getAttribute("xlink:href") || "";
        try {
          const path = xmlResourcePath(href);
          // Package images can be local object URLs, so Next Image cannot optimize them.
          // eslint-disable-next-line @next/next/no-img-element
          return <img key={key} src={pkg.source.resourceUrl(path)} alt={element.parentElement?.querySelector("caption")?.textContent?.trim() || "Document figure"} loading="lazy" />;
        } catch { return <p key={key} className="resource-error">无法加载不安全的资源路径</p>; }
      }
      case "table-wrap": return <div key={key}>{addressable(element, <figure className="table-wrap">{content}</figure>)}</div>;
      case "table": return <div key={key} className="table-scroll"><table>{content}</table></div>;
      case "thead": return <thead key={key}>{content}</thead>;
      case "tbody": return <tbody key={key}>{content}</tbody>;
      case "tr": return <tr key={key}>{content}</tr>;
      case "th": return <th key={key} rowSpan={Number(element.getAttribute("rowspan")) || undefined} colSpan={Number(element.getAttribute("colspan")) || undefined}>{addressable(element, content)}</th>;
      case "td": return <td key={key} rowSpan={Number(element.getAttribute("rowspan")) || undefined} colSpan={Number(element.getAttribute("colspan")) || undefined}>{addressable(element, content)}</td>;
      case "inline-formula":
      case "disp-formula": {
        const tex = directText(element, "tex-math");
        let html = "";
        try { html = katex.renderToString(tex, { displayMode: name === "disp-formula", throwOnError: false, strict: "warn", trust: false }); }
        catch { html = tex; }
        const math = <span className={name === "disp-formula" ? "display-math" : "inline-math"} dangerouslySetInnerHTML={{ __html: html }} />;
        return <span key={key}>{addressable(element, math)}</span>;
      }
      case "tex-math": return null;
      case "xref": {
        const rid = element.getAttribute("rid");
        return <a key={key} href={rid ? `#${rid}` : undefined} className="xref">{content}</a>;
      }
      case "ext-link":
      case "uri": {
        const href = element.getAttributeNS(XLINK, "href") || element.textContent?.trim() || "";
        const safe = /^https?:\/\//i.test(href) ? href : undefined;
        return <a key={key} href={safe} target="_blank" rel="noreferrer">{content}</a>;
      }
      case "ref-list": return <section key={key} className="references"><h2>{directText(element, ":scope > title") || "References"}</h2><ol>{renderChildren(element, ["title"])}</ol></section>;
      case "ref": return <li key={key}>{addressable(element, renderChildren(element, ["label"]))}</li>;
      case "fn": return <aside key={key} className="footnote">{addressable(element, content)}</aside>;
      case "preformat":
      case "code": return <div key={key}>{addressable(element, <pre><code>{element.textContent}</code></pre>)}</div>;
      case "break": return <br key={key} />;
      case "label": return <span key={key} className="label">{content}</span>;
      case "article-id":
      case "journal-id":
      case "issn":
      case "pub-date":
      case "kwd-group": return <span key={key} className={`metadata-field ${name}`}>{addressable(element, content)}</span>;
      default: return element.id ? <span key={key}>{addressable(element, content, `p2h-${name}`)}</span> : <span key={key} className={`p2h-${name}`}>{content}</span>;
    }
  };

  return <>{renderNode(pkg.xml.documentElement, "root")}</>;
}
