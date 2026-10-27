// The tab area beside the thread (DESKTOP_PARITY §2.1, §2.8): the strip of tabs (Review, files),
// the file tree on its left when shown (Ctrl+Shift+E), and the workspace layout (split, full,
// hidden). Every open tab stays mounted, the ones not in front hidden, so a review comment being
// written or a file's scroll survives a switch.

import { useEffect, useRef, useState, type ReactNode } from "react";

import { Icon } from "./icons";
import { tabLabel, type WorkspaceTab } from "./tabs";
import { shortcutLabel } from "./keymap";

export interface TabAreaProps {
  tabs: WorkspaceTab[];
  active: string | null;
  onSelect: (key: string) => void;
  onClose: (key: string) => void;
  /** The file tree, when it is shown. */
  tree: ReactNode | null;
  treeShown: boolean;
  onToggleTree: (() => void) | null;
  workspaceLayout: "split" | "tabs" | "chat";
  setWorkspaceLayout: (layout: "split" | "tabs" | "chat") => void;
  render: (tab: WorkspaceTab) => ReactNode;
  /** New tab's menu (DESKTOP §16 B28): a terminal in the tab area, a preview; null for one this window cannot open. */
  onNewTab?: { terminal: (() => void) | null; preview: (() => void) | null };
}

export function TabArea(props: TabAreaProps) {
  const front = props.tabs.find((tab) => tab.key === props.active) ?? props.tabs[0] ?? null;
  const strip = useRef<HTMLSpanElement>(null);
  const [newMenu, setNewMenu] = useState(false);
  const newChoices: [string, () => void][] = [];
  if (props.onNewTab?.terminal) newChoices.push(["Terminal", props.onNewTab.terminal]);
  if (props.onNewTab?.preview) newChoices.push(["Preview", props.onNewTab.preview]);
  // The tab in front stays in sight when the strip scrolls.
  useEffect(() => {
    strip.current?.querySelector(".tab.selected")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [front?.key, props.tabs.length]);
  return (
    <aside className={`review-pane tab-area${props.tree ? " with-tree" : ""}`} aria-label={front ? tabLabel(front) : "Files"}>
      <header className="review-bar">
        {props.onToggleTree ? (
          <button className={`icon bar-action${props.treeShown ? " active" : ""}`} aria-pressed={props.treeShown} aria-label="File tree" title={`The file tree (${shortcutLabel("fileTree")})`} onClick={props.onToggleTree}><Icon name="folderTree" size={14} /></button>
        ) : null}
        <span className="tab-strip" role="tablist" aria-label="Tabs" ref={strip}>
          {props.tabs.map((tab) => {
            const label = tabLabel(tab);
            const selected = tab.key === front?.key;
            return (
              <span key={tab.key} className={`tab${selected ? " selected" : ""}`} role="tab" aria-selected={selected} title={tab.kind === "file" ? tab.path : tab.kind === "preview" ? tab.url : label} data-path={tab.kind === "file" ? tab.path : undefined}
                onClick={() => props.onSelect(tab.key)} onAuxClick={(event) => { if (event.button === 1) props.onClose(tab.key); }}>
                <Icon name={tab.kind === "review" ? "fileDiff" : tab.kind === "preview" ? "globe" : tab.kind === "prs" ? "gitPullRequest" : tab.kind === "terminal" ? "terminal" : "file"} size={13} /> <span className="tab-label">{label}</span>
                <button className="icon" aria-label={`Close the ${label} tab`} title={`Close (${shortcutLabel("closeTab")})`} onClick={(event) => { event.stopPropagation(); props.onClose(tab.key); }}><Icon name="x" size={12} /></button>
              </span>
            );
          })}
        </span>
        {newChoices.length ? (
          <span className="new-tab">
            <button className="icon bar-action" aria-label="New tab" aria-haspopup="menu" aria-expanded={newMenu} title="New tab" onClick={() => setNewMenu((open) => !open)}><Icon name="plus" size={14} /></button>
            {newMenu ? (
              <span className="menu new-tab-menu" role="menu" onMouseLeave={() => setNewMenu(false)}>
                {newChoices.map(([label, run]) => <button key={label} role="menuitem" onClick={() => { setNewMenu(false); run(); }}>{label}</button>)}
              </span>
            ) : null}
          </span>
        ) : null}
        <span className="spacer" />
        <span className="segmented layout-switch" role="radiogroup" aria-label="Layout">
          <button role="radio" aria-checked={props.workspaceLayout === "split"} className={props.workspaceLayout === "split" ? "selected" : ""} title={`Beside the thread (${shortcutLabel("cycleLayout")} cycles)`} onClick={() => props.setWorkspaceLayout("split")}>Split</button>
          <button role="radio" aria-checked={props.workspaceLayout === "tabs"} className={props.workspaceLayout === "tabs" ? "selected" : ""} title={`The tabs in full (${shortcutLabel("fullView")})`} onClick={() => props.setWorkspaceLayout("tabs")}>Full</button>
          <button role="radio" aria-checked={false} title={`Hide the tabs, keep them open (${shortcutLabel("chatTabsToggle")})`} onClick={() => props.setWorkspaceLayout("chat")}>Hide</button>
        </span>
      </header>
      <div className="tab-columns">
        {props.tree}
        <div className="tab-bodies">
          {props.tabs.map((tab) => (
            <div key={tab.key} className="tab-body" role="tabpanel" aria-label={tabLabel(tab)} hidden={tab.key !== front?.key}>{props.render(tab)}</div>
          ))}
          {props.tabs.length === 0 ? <p className="empty small">Open a file from the tree.</p> : null}
        </div>
      </div>
    </aside>
  );
}
