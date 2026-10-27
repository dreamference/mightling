// The preview tab (DESKTOP_PARITY §2.9, Phase 3 item 4): a page of the user's own (a dev server on
// this machine, a page on the LAN, a file) beside the thread. The app lays a view of its own over
// this tab's body (desktop/electron/src/preview.ts), so this component draws the toolbar and keeps
// the view where the body is, hidden whenever something covers it. A site out on the internet
// asks first (Allow once, Always allow, Block); `/airgapped on` keeps it to this machine. Comment
// mode (Ctrl+Shift+.) picks an element in the page, or an area dragged over it, and sends a comment
// on it to the composer; several can wait and go together (DESKTOP §16 B29). The address suggests
// the tab's own history, nothing else (no search).

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import * as bridge from "./bridge";
import { copyText } from "./clipboard";
import { Icon } from "./icons";
import { keyAction, matches, shortcutLabel } from "./keymap";

export interface PreviewTabProps {
  id: string;
  url: string;
  thread: string | null;
  /** In front, in a shown tab area, and nothing (a dialog) over it. */
  visible: boolean;
  /** Bumped when the layout around the tab may have moved it (the sidebar, the split, the text size). */
  layoutKey: string;
  devTools: boolean;
  onTitle: (title: string | null, url: string) => void;
  onAlways: (site: string, choice: "allow" | "block") => void;
  onComment: (text: string) => void;
  /** A shortcut pressed inside the page that the tab area answers (Ctrl+W, Ctrl+T). */
  onShortcut: (action: string) => void;
  notice: (error: unknown) => void;
}

export function PreviewTab(props: PreviewTabProps) {
  const { id, notice } = props;
  const surface = useRef<HTMLDivElement>(null);
  const address = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState(props.url);
  const [state, setState] = useState<bridge.PreviewEvent | null>(null);
  const [ask, setAsk] = useState<{ site: string; reason: string } | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<bridge.PickedElement | null>(null);
  const [comment, setComment] = useState("");
  /** Comments written and waiting to go together (DESKTOP §16 B29). */
  const [waiting, setWaiting] = useState<string[]>([]);
  /** The addresses this tab has shown, newest first: the address box's suggestions. */
  const [visited, setVisited] = useState<string[]>(props.url ? [props.url] : []);
  const opened = useRef(false);
  const available = bridge.preview.available();
  /** Puts the view over the tab's body (set by the layout effect below). */
  const placeView = useRef<() => void>(() => {});

  const go = useCallback((target: string) => {
    const url = addressOf(target);
    if (!url) return;
    setAsk(null);
    setBlocked(null);
    setTyped(url);
    // A tab opened empty (Ctrl+T) has no view until its first address; placed when the view exists,
    // or it stayed 0×0 and hidden until the layout changed (a Mac, where the view took longer to come
    // than the layout effect's one late placement).
    (opened.current ? bridge.preview.navigate(id, url, props.thread) : bridge.preview.open(id, url, props.thread))
      .then(() => placeView.current())
      .catch(notice);
    opened.current = true;
  }, [id, props.thread, notice]);

  // The view lives as long as the tab.
  useEffect(() => {
    if (!available) return;
    if (props.url) go(props.url);
    else address.current?.focus();
    return () => {
      bridge.preview.close(id).catch(() => {});
    };
  }, [id, available]);

  // The page's news.
  const handlers = useRef(props);
  handlers.current = props;
  useEffect(() => bridge.preview.listen((event) => {
    if (event.id !== id) return;
    if (event.kind === "state") {
      setState(event);
      if (event.url && document.activeElement !== address.current) setTyped(event.url);
      if (event.url) setVisited((list) => remembered(list, event.url as string));
      handlers.current.onTitle(event.title || null, event.url ?? "");
    } else if (event.kind === "ask" && event.site) {
      setAsk({ site: event.site, reason: event.reason ?? "" });
    } else if (event.kind === "blocked") {
      setBlocked(event.reason ?? "Blocked.");
    } else if (event.kind === "download") {
      handlers.current.notice(`Saving a download from ${event.site || "the preview"}: choose where in the dialog.`);
    } else if (event.kind === "key" && event.key) {
      runKeyRef.current(event.key);
    }
  }), [id]);

  // Hidden while something covers it; otherwise exactly over the body.
  const covered = !props.visible || picked !== null;
  useLayoutEffect(() => {
    const element = surface.current;
    if (!available || !element) return;
    const place = () => {
      // The text size is CSS zoom on the page's root, and the rect comes back unzoomed: scale it to the window's pixels.
      const zoom = (element as HTMLElement & { currentCSSZoom?: number }).currentCSSZoom ?? 1;
      const rect = element.getBoundingClientRect();
      bridge.preview.place(id, { x: rect.left * zoom, y: rect.top * zoom, width: rect.width * zoom, height: rect.height * zoom }, !covered && rect.width > 0 && rect.height > 0).catch(() => {});
    };
    placeView.current = place;
    place();
    const observer = new ResizeObserver(place);
    observer.observe(element);
    window.addEventListener("resize", place);
    // A layout change elsewhere (the sidebar folding) moves the body without resizing it.
    const timer = setTimeout(place, 360);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
      clearTimeout(timer);
    };
  }, [id, covered, props.layoutKey, available, ask, blocked, picking]);

  const pick = async () => {
    if (picking) return;
    setPicking(true);
    try {
      const element = await bridge.preview.pick(id);
      if (element) {
        setPicked(element);
        setComment("");
      }
    } catch (error) {
      notice(error);
    } finally {
      setPicking(false);
    }
  };

  /** The comment on what was picked, as the composer gets it; then sent with those waiting, or kept to add another. */
  const sendComment = (another = false) => {
    if (!picked || !comment.trim()) return;
    const line = commentLine(picked, comment);
    setPicked(null);
    if (another) {
      setWaiting((list) => [...list, line]);
      void pick();
      return;
    }
    props.onComment([...waiting, line].join("\n\n"));
    setWaiting([]);
  };

  const runKeyRef = useRef<(action: string) => void>(() => {});
  const runKey = (action: string) => {
    if (action === "previewBack") bridge.preview.go(id, "back").catch(notice);
    else if (action === "previewForward") bridge.preview.go(id, "forward").catch(notice);
    else if (action === "previewReload") bridge.preview.reload(id).catch(notice);
    else if (action === "previewReloadHard") bridge.preview.reload(id, true).catch(notice);
    else if (action === "addressBar") address.current?.select();
    else if (action === "commentMode") void pick();
    else if (action === "copyUrl") void copyText(state?.url ?? typed);
    else handlers.current.onShortcut(action);
  };
  runKeyRef.current = runKey;

  // The preview's keys while the window's page has the focus and this tab is in front.
  useEffect(() => {
    if (!props.visible) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const bound: [ReturnType<typeof keyAction>, string][] = [
        [keyAction("addressBar"), "addressBar"],
        [keyAction("previewReload"), "previewReload"],
        [keyAction("previewReloadHard"), "previewReloadHard"],
        [keyAction("previewBack"), "previewBack"],
        [keyAction("previewForward"), "previewForward"],
        [keyAction("commentMode"), "commentMode"],
        [keyAction("copyUrl"), "copyUrl"],
      ];
      const hit = bound.find(([action]) => matches(event, action));
      if (!hit) return;
      event.preventDefault();
      runKey(hit[1]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!available) {
    return <p className="empty small">The preview tab is the desktop app's: open the page in your browser.</p>;
  }

  return (
    <div className="preview-tab">
      <div className="preview-tools">
        <button className="icon" aria-label="Back" title="Back (Alt+←)" disabled={!state?.canGoBack} onClick={() => bridge.preview.go(id, "back").catch(notice)}><Icon name="chevronLeft" size={14} /></button>
        <button className="icon" aria-label="Forward" title="Forward (Alt+→)" disabled={!state?.canGoForward} onClick={() => bridge.preview.go(id, "forward").catch(notice)}><Icon name="chevronRight" size={14} /></button>
        <button className="icon" aria-label="Reload" title={`Reload (${shortcutLabel("previewReload")})`} onClick={() => bridge.preview.reload(id).catch(notice)}><Icon name="refresh" size={14} /></button>
        <form className="address" onSubmit={(event) => { event.preventDefault(); go(typed); }}>
          <input ref={address} value={typed} placeholder="localhost:5173, a LAN page, or a file" aria-label="Address" list={`preview-history-${id}`}
            spellCheck={false} onChange={(event) => setTyped(event.target.value)} />
          <datalist id={`preview-history-${id}`}>{visited.map((url) => <option key={url} value={url} />)}</datalist>
        </form>
        <button className={`icon${picking ? " active" : ""}`} aria-pressed={picking} aria-label="Comment on the page" title={`Comment on an element of the page (${shortcutLabel("commentMode")})`} onClick={() => void pick()}><Icon name="messageSquare" size={14} /></button>
        <button className="icon" aria-label="Copy the address" title={`Copy the address (${shortcutLabel("copyUrl")})`} onClick={() => void copyText(state?.url ?? typed)}><Icon name="copy" size={14} /></button>
        {props.devTools ? <button className="icon" aria-label="Developer tools" title="Developer tools (Developer mode is on)" onClick={() => bridge.preview.devTools(id).catch(notice)}><Icon name="code" size={14} /></button> : null}
      </div>
      {picking ? <div className="preview-note">Click an element of the page to comment on it; Esc in the page stops.</div> : null}
      {ask ? (
        <div className="preview-ask" role="alert">
          <span><strong>{ask.site}</strong> is on the internet. Load it in the preview?</span>
          <button onClick={() => { setAsk(null); bridge.preview.allowOnce(id, ask.site).catch(notice); }}>Allow once</button>
          <button onClick={() => { props.onAlways(ask.site, "allow"); setAsk(null); setTimeout(() => bridge.preview.reload(id).catch(notice), 50); }}>Always allow</button>
          <button className="danger" onClick={() => { props.onAlways(ask.site, "block"); setAsk(null); }}>Block</button>
        </div>
      ) : null}
      {blocked ? <div className="preview-blocked" role="status">{blocked}<button className="icon" aria-label="Dismiss" onClick={() => setBlocked(null)}><Icon name="x" size={12} /></button></div> : null}
      {state?.error ? <div className="preview-blocked" role="status">The page did not load: {state.error}</div> : null}
      {waiting.length && !picked && !picking ? (
        <div className="preview-note" role="status">
          {waiting.length} comment{waiting.length === 1 ? "" : "s"} waiting.{" "}
          <button className="link-button" onClick={() => { props.onComment(waiting.join("\n\n")); setWaiting([]); }}>Send {waiting.length === 1 ? "it" : "them"} to the composer</button>{" "}
          <button className="link-button" onClick={() => setWaiting([])}>Drop</button>
        </div>
      ) : null}
      {picked ? (
        <div className="preview-comment">
          <div className="picked">{picked.area ? <span>An area of {picked.area.width} × {picked.area.height} px, around <code>{picked.selector}</code></span> : <><code>{picked.selector}</code>{picked.text ? <span> “{picked.text}”</span> : null}</>}</div>
          <textarea autoFocus rows={3} value={comment} placeholder="What should change here?" aria-label="Comment on the element"
            onChange={(event) => setComment(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) sendComment();
              if (event.key === "Escape") setPicked(null);
            }} />
          <div className="buttons">
            <button className="primary" disabled={!comment.trim()} onClick={() => sendComment()}>{waiting.length ? `Send all ${waiting.length + 1} to the composer` : "Send to the composer"}</button>
            <button disabled={!comment.trim()} onClick={() => sendComment(true)}>Add another</button>
            <button onClick={() => setPicked(null)}>Cancel</button>
          </div>
        </div>
      ) : null}
      <div className="preview-surface" ref={surface} aria-label="The previewed page" role="region">
        {!state && !props.url ? <p className="empty small">Type an address: a dev server here (localhost:5173), a page on the LAN, or a file path.</p> : null}
      </div>
    </div>
  );
}

/** The address bar's text as a URL (preview-rules.ts `addressOf` is the app's copy of the rule). */
/** How many addresses a tab's history keeps for its suggestions. */
export const HISTORY_KEPT = 30;

/** The tab's history with `url` first, once, capped. */
export function remembered(list: string[], url: string): string[] {
  return [url, ...list.filter((entry) => entry !== url)].slice(0, HISTORY_KEPT);
}

/** One comment as the composer gets it: the page, what was picked (an element or an area), and what to change. */
export function commentLine(picked: { selector: string; text: string; url: string; area?: { x: number; y: number; width: number; height: number } }, comment: string): string {
  if (picked.area) {
    const { x, y, width, height } = picked.area;
    return `On the page ${picked.url}, the area of ${width} × ${height} px at (${x}, ${y}), around \`${picked.selector}\`: ${comment.trim()}`;
  }
  const where = picked.text ? `\`${picked.selector}\` ("${picked.text}")` : `\`${picked.selector}\``;
  return `On the page ${picked.url}, the element ${where}: ${comment.trim()}`;
}

export function addressOf(typed: string): string | null {
  const text = typed.trim();
  if (!text) return null;
  if (text.startsWith("/")) return `file://${text}`;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text) || /^(about|data):/i.test(text)) return text;
  const host = text.split(/[/:?#]/)[0].toLowerCase();
  const local = host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || !host.includes(".") || /^(127|10|192\.168|172\.(1[6-9]|2\d|3[01]))\./.test(host);
  return `${local ? "http" : "https"}://${text}`;
}
