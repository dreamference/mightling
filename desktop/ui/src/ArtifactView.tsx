// The artifacts viewer (DESKTOP_PARITY §2.8, Phase 3 item 3): a file tab that shows a document as
// it reads, beside the chat. Markdown rendered as in messages (local images shown, local links
// opened in a tab), images, PDFs drawn page by page with pdf.js, and HTML sanitised into a shadow
// root (no script, no form, no navigation; the preview tab runs a page for real). A Source switch
// shows the text in the ordinary file view; LaTeX has only its source, since `tectonic` is not
// bundled (§7 question 4). The bytes come from the server's `fs/readFile`, followed with `fs/watch`.

import { useCallback, useEffect, useRef, useState } from "react";

import * as bridge from "./bridge";
import { artifactKind, hasSource, imageType, localReference, sanitizeDocument, type ArtifactKind } from "./artifacts";
import { FileView, type FileViewProps } from "./FileView";
import { bytesOf, sizeLabel } from "./files";
import { Icon } from "./icons";
import { renderDocument } from "./markdown";

/** Documents bigger than this ask before they are read (a PDF of scans can be large). */
const LARGEST_DOCUMENT = 32 * 1024 * 1024;

type Bytes = { status: "loading" } | { status: "error"; message: string } | { status: "large"; bytes: number } | { status: "ready"; data: Uint8Array<ArrayBuffer>; stamp: number };

let nextWatch = 1;

/** The file's bytes through the server, read again when it changes. */
function useFileBytes(props: FileViewProps): [Bytes, () => void, () => void] {
  const { path, client, listen } = props;
  const [bytes, setBytes] = useState<Bytes>({ status: "loading" });
  const [force, setForce] = useState(false);
  const load = useCallback(async () => {
    try {
      const size = await bridge.fileSize(path).catch(() => null);
      if (size !== null && size > LARGEST_DOCUMENT && !force) return setBytes({ status: "large", bytes: size });
      const answer = await client.request("fs/readFile", { path });
      setBytes({ status: "ready", data: bytesOf(answer.dataBase64), stamp: Date.now() });
    } catch (error) {
      setBytes({ status: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }, [path, client, force]);
  useEffect(() => {
    setBytes({ status: "loading" });
    void load();
  }, [load]);
  useEffect(() => {
    const watchId = `artifact${nextWatch++}`;
    client.request("fs/watch", { watchId, path }).catch(() => {});
    let timer: ReturnType<typeof setTimeout> | null = null;
    const stop = listen((notification) => {
      if (notification.method !== "fs/changed" || notification.params.watchId !== watchId) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void load(), 200);
    });
    return () => {
      stop();
      if (timer) clearTimeout(timer);
      client.request("fs/unwatch", { watchId }).catch(() => {});
    };
  }, [path, client, listen, load]);
  return [bytes, () => void load(), () => setForce(true)];
}

export function ArtifactView(props: FileViewProps & { onOpenFile: (path: string) => void }) {
  const kind = artifactKind(props.path) as ArtifactKind;
  const [mode, setMode] = useState<"rendered" | "source">(kind === "latex" ? "source" : "rendered");
  const sourceAvailable = hasSource(props.path);
  return (
    <div className="artifact-view">
      {sourceAvailable && kind !== "latex" ? (
        <span className="segmented artifact-mode" role="radiogroup" aria-label="Show">
          <button role="radio" aria-checked={mode === "rendered"} className={mode === "rendered" ? "selected" : ""} onClick={() => setMode("rendered")}>Rendered</button>
          <button role="radio" aria-checked={mode === "source"} className={mode === "source" ? "selected" : ""} onClick={() => setMode("source")}>Source</button>
        </span>
      ) : null}
      {kind === "latex" ? <p className="artifact-note">A LaTeX source: its PDF preview needs the <code>tectonic</code> compiler, which this build does not bundle. Compile it in the terminal and open the PDF.</p> : null}
      {mode === "source" ? <FileView {...props} /> : <Rendered {...props} kind={kind} />}
    </div>
  );
}

function Rendered(props: FileViewProps & { kind: ArtifactKind; onOpenFile: (path: string) => void }) {
  const [bytes, reload, force] = useFileBytes(props);
  if (bytes.status === "loading") return <p className="empty small">Reading…</p>;
  if (bytes.status === "error") return <div className="notice">{bytes.message}</div>;
  if (bytes.status === "large") {
    return <p className="empty small">This file is {sizeLabel(bytes.bytes)}. <button className="link-button" onClick={force}>Show it anyway</button></p>;
  }
  const tools = (
    <div className="file-tools artifact-tools">
      <span className="file-path" title={props.path}>{props.root && props.path.startsWith(`${props.root.replace(/\/+$/, "")}/`) ? props.path.slice(props.root.replace(/\/+$/, "").length + 1) : props.path}</span>
      <span className="file-meta">{sizeLabel(bytes.data.length)}</span>
      <button className="link-button" title="Name this file in the composer" onClick={() => props.onMention(props.path)}>Mention</button>
      <button className="icon" title="Read the file again" aria-label="Reload the file" onClick={reload}><Icon name="undo" size={13} /></button>
    </div>
  );
  switch (props.kind) {
    case "image":
      return <>{tools}<ImageArtifact data={bytes.data} path={props.path} /></>;
    case "pdf":
      return <>{tools}<PdfArtifact data={bytes.data} stamp={bytes.stamp} /></>;
    case "markdown":
    case "html":
      return <>{tools}<DocumentArtifact {...props} data={bytes.data} kind={props.kind} /></>;
    default:
      return null;
  }
}

function ImageArtifact(props: { data: Uint8Array<ArrayBuffer>; path: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [fit, setFit] = useState(true);
  const [size, setSize] = useState<string | null>(null);
  useEffect(() => {
    const made = URL.createObjectURL(new Blob([props.data], { type: imageType(props.path) }));
    setUrl(made);
    return () => URL.revokeObjectURL(made);
  }, [props.data, props.path]);
  return (
    <div className={`image-artifact${fit ? " fit" : ""}`} onClick={() => setFit(!fit)} title={fit ? "Click for the actual size" : "Click to fit"}>
      {url ? <img src={url} alt={props.path.split("/").pop()} onLoad={(event) => setSize(`${event.currentTarget.naturalWidth} × ${event.currentTarget.naturalHeight}`)} /> : null}
      {size ? <span className="image-size">{size}</span> : null}
    </div>
  );
}

function PdfArtifact(props: { data: Uint8Array<ArrayBuffer>; stamp: number }) {
  const pages = useRef<HTMLDivElement>(null);
  const [count, setCount] = useState<number | null>(null);
  /** The zoom; null fits the page to the pane's width. */
  const [scale, setScale] = useState<number | null>(null);
  const [shown, setShown] = useState(1);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const holder = pages.current;
    void (async () => {
      try {
        // pdf.js and its worker are loaded only when a PDF is opened; both are in the bundle.
        const [pdfjs, worker] = await Promise.all([import("pdfjs-dist"), import("pdfjs-dist/build/pdf.worker.min.mjs?url")]);
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
        // A copy: pdf.js hands the buffer to its worker, and the bytes stay ours for a reload.
        const pdf = await pdfjs.getDocument({ data: props.data.slice(), isEvalSupported: false, enableXfa: false, useSystemFonts: true }).promise;
        if (cancelled || !holder) return;
        setCount(pdf.numPages);
        holder.replaceChildren();
        const first = await pdf.getPage(1);
        const fit = Math.max(0.25, (holder.clientWidth - 32) / first.getViewport({ scale: 1 }).width);
        const zoom = scale ?? fit;
        setShown(zoom);
        for (let number = 1; number <= Math.min(pdf.numPages, 300); number++) {
          const page = number === 1 ? first : await pdf.getPage(number);
          if (cancelled) return;
          const viewport = page.getViewport({ scale: zoom * (window.devicePixelRatio || 1) });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = `${viewport.width / (window.devicePixelRatio || 1)}px`;
          canvas.setAttribute("aria-label", `Page ${number}`);
          canvas.className = "pdf-page";
          holder.append(canvas);
          await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
        }
        await pdf.destroy();
      } catch (failure) {
        if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.data, props.stamp, scale]);
  return (
    <div className="pdf-artifact">
      <div className="pdf-tools">
        <span>{count === null ? "Reading the PDF…" : `${count} page${count === 1 ? "" : "s"}${count > 300 ? ", the first 300 shown" : ""}`}</span>
        <span className="spacer" />
        <button className="icon" aria-label="Smaller" title="Smaller" onClick={() => setScale(Math.max(0.25, (scale ?? shown) - 0.25))}>−</button>
        <button className="link-button pdf-scale" title="Fit the width" onClick={() => setScale(null)}>{scale === null ? "Fit" : `${Math.round(scale * 100)}%`}</button>
        <button className="icon" aria-label="Bigger" title="Bigger" onClick={() => setScale(Math.min(4, (scale ?? shown) + 0.25))}>+</button>
      </div>
      {error ? <div className="notice">This PDF could not be shown: {error}</div> : null}
      <div className="pdf-pages" ref={pages} role="document" aria-label="PDF" />
    </div>
  );
}

/** Markdown or HTML: rendered into a shadow root, local images read through the server, local links opened in a tab. */
function DocumentArtifact(props: FileViewProps & { data: Uint8Array<ArrayBuffer>; kind: "markdown" | "html"; onOpenFile: (path: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [removed, setRemoved] = useState(0);
  const { client, path, onOpenFile } = props;
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const shadow = element.shadowRoot ?? element.attachShadow({ mode: "open" });
    const text = new TextDecoder().decode(props.data);
    const urls: string[] = [];
    let cancelled = false;
    if (props.kind === "markdown") {
      shadow.innerHTML = `<style>${DOCUMENT_STYLE}</style><article class="doc">${renderDocument(text)}</article>`;
      setRemoved(0);
    } else {
      const doc = new DOMParser().parseFromString(text, "text/html");
      setRemoved(sanitizeDocument(doc, path));
      const article = document.createElement("article");
      article.className = "doc html";
      const base = document.createElement("style");
      base.textContent = HTML_BASE_STYLE;
      // The page's own styles, then its body, moved over as nodes (never re-parsed from text).
      const styles = [...doc.querySelectorAll("style")].map((style) => document.importNode(style, true));
      for (const child of [...doc.body.childNodes]) article.append(document.importNode(child, true));
      shadow.replaceChildren(base, ...styles, article);
    }
    // Images: a local one read through the server; a remote one stays its alt text (nothing leaves the machine).
    for (const image of [...shadow.querySelectorAll<HTMLImageElement>("img[data-ref], img[data-local]")]) {
      const local = image.dataset.local ?? localReference(path, image.dataset.ref ?? "");
      if (!local) {
        image.replaceWith(Object.assign(document.createElement("span"), { className: "image-alt", textContent: `[${image.alt || "image"}]` }));
        continue;
      }
      client.request("fs/readFile", { path: local }).then((answer) => {
        if (cancelled) return;
        const url = URL.createObjectURL(new Blob([bytesOf(answer.dataBase64)], { type: imageType(local) }));
        urls.push(url);
        image.src = url;
      }).catch(() => image.replaceWith(Object.assign(document.createElement("span"), { className: "image-alt", textContent: `[${image.alt || local}]` })));
    }
    // Links: a local file opens in a tab; anything else is not followed.
    const onClick = (event: Event) => {
      const target = (event.target as HTMLElement).closest<HTMLElement>("[data-href]");
      if (!target) return;
      event.preventDefault();
      const local = localReference(path, target.dataset.href ?? "");
      if (local) onOpenFile(local);
    };
    shadow.addEventListener("click", onClick);
    return () => {
      cancelled = true;
      shadow.removeEventListener("click", onClick);
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [props.data, props.kind, path, client, onOpenFile]);
  return (
    <div className="document-artifact">
      {props.kind === "html" ? (
        <p className="artifact-note">A preview without scripts{removed ? `: ${removed} script, form or active part${removed === 1 ? "" : "s"} left out` : ""}. Run the page in the preview tab to see it work.</p>
      ) : null}
      <div className="document-host" ref={host} role="document" aria-label={props.kind === "markdown" ? "Markdown" : "HTML"} />
    </div>
  );
}

/** The rendered Markdown's look inside its shadow root: the message styles' essentials, from the tokens. */
const DOCUMENT_STYLE = `
.doc { font: 15px/1.6 var(--font-ui); color: var(--text); max-width: 760px; padding: 4px 24px 32px; }
.doc h1, .doc h2, .doc h3 { font-weight: 600; line-height: 1.25; margin: 1.2em 0 0.5em; }
.doc h1 { font-size: 22px; } .doc h2 { font-size: 18px; } .doc h3 { font-size: 15px; }
.doc code { font: 13px var(--font-code); background: var(--code-bg); padding: 1px 4px; border-radius: 4px; }
.doc pre { background: var(--code-bg); padding: 10px 12px; border-radius: 8px; overflow: auto; }
.doc pre code { background: none; padding: 0; }
.doc table { border-collapse: collapse; } .doc th, .doc td { border: 1px solid var(--line); padding: 4px 8px; }
.doc blockquote { margin: 0; padding-left: 12px; border-left: 3px solid var(--line); color: var(--muted); }
.doc img { max-width: 100%; }
.doc .link { color: var(--accent); cursor: pointer; text-decoration: underline; }
.image-alt { color: var(--muted); }
`;

/** An HTML document's starting point; its own styles come after and win. */
const HTML_BASE_STYLE = `
.doc.html { display: block; font: 15px/1.5 system-ui, sans-serif; color: #111; background: #fff; padding: 12px 16px 32px; }
a[data-href] { color: #0b5cad; text-decoration: underline; cursor: pointer; }
img { max-width: 100%; }
.image-alt { color: #777; }
`;
