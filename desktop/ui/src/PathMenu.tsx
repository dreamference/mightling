// One menu on every path (DESKTOP_PARITY_CLAUDE gap 10, DESKTOP §16 B14): a right-click on a file's
// path in the stream, the review pane, the file tree or a file tab offers Attach (an `@` mention in
// Work's composer), Open in the editor, Show in the folder and Copy path. An element names its file
// with `data-path` (absolute); the window's own context-menu handler opens this instead of the native one.
import { useEffect, useRef } from "react";

export interface PathMenuState {
  x: number;
  y: number;
  path: string;
}

export type PathAction = "attach" | "editor" | "folder" | "copy";

/** The absolute path a right-clicked element names, or null. */
export function pathOf(target: EventTarget | null): string | null {
  const closest = (target as Element | null)?.closest;
  const element = typeof closest === "function" ? (target as Element).closest<HTMLElement>("[data-path]") : null;
  const path = element?.dataset.path ?? "";
  return path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path) ? path : null;
}

/** Where the menu opens so it stays inside the window. */
export function menuPosition(x: number, y: number, width: number, height: number, viewport: { width: number; height: number }): { left: number; top: number } {
  return { left: Math.max(4, Math.min(x, viewport.width - width - 4)), top: Math.max(4, Math.min(y, viewport.height - height - 4)) };
}

export function PathMenu(props: { menu: PathMenuState; canAttach: boolean; canReveal: boolean; onAction: (action: PathAction) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      if (event.type === "mousedown" && ref.current?.contains(event.target as Node)) return;
      props.onClose();
    };
    window.addEventListener("mousedown", close, true);
    window.addEventListener("keydown", close, true);
    window.addEventListener("blur", props.onClose);
    return () => {
      window.removeEventListener("mousedown", close, true);
      window.removeEventListener("keydown", close, true);
      window.removeEventListener("blur", props.onClose);
    };
  }, [props.onClose]);
  const at = menuPosition(props.menu.x, props.menu.y, 220, 150, { width: window.innerWidth, height: window.innerHeight });
  const item = (action: PathAction, label: string) => (
    <button role="menuitem" onClick={() => { props.onClose(); props.onAction(action); }}>{label}</button>
  );
  return (
    <div ref={ref} className="menu path-menu" role="menu" aria-label={props.menu.path} style={{ position: "fixed", left: at.left, top: at.top }}>
      {props.canAttach ? item("attach", "Attach to the message") : null}
      {item("editor", "Open in the editor")}
      {props.canReveal ? item("folder", "Show in the folder") : null}
      {item("copy", "Copy path")}
    </div>
  );
}
