// Phone and Telegram Mini App Work layout and navigation helpers (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §10 G5, G12; specs/DREAMFERENCE_MIGHTLING_TELEGRAM_MOONSHOT.md §15).

import { createElement } from "react";

import * as bridge from "./bridge";
import { FileTree } from "./FileTree";
import type { RpcClient } from "./rpc";
import type { Listen } from "./turns";

export const NARROW_MEDIA_QUERY = "(max-width: 720px)";

/** Active layers in `App.tsx`, ordered by what the Telegram back button closes first (CHAT_REPOS §10 G5). */
export interface BackLayers {
  /** G8 full-screen command output view (`outputTarget !== null`). */
  outputTarget?: boolean | unknown;
  /** G4 Repos view (`reposOpen`). */
  reposOpen?: boolean;
  /** An open dialog or overlay that already closes on Escape. */
  dialogOpen?: boolean;
  /** Review pane or file tab sheet (`tabsShown`). */
  sheetOpen?: boolean;
  /** Alias for `sheetOpen` (`tabsShown`). */
  tabsShown?: boolean;
  /** The sidebar drawer (`drawer`). */
  drawer?: boolean;
  /** A selected thread (`selected !== null`). */
  threadSelected?: boolean;
  /** Alias for `threadSelected` (`selected`). */
  selected?: boolean | unknown;
  /** Whether the screen is narrow (`(max-width: 720px)`). */
  narrow?: boolean;
}

export type BackTarget = "output" | "repos" | "dialog" | "sheet" | "drawer" | "thread";

/**
 * Decides which layer the Telegram back button closes, from top-most to bottom-most:
 * 1. `"output"` — the output view (`outputTarget`)
 * 2. `"repos"` — the Repos view (`reposOpen`)
 * 3. `"dialog"` — an open dialog that closes on Escape
 * 4. `"sheet"` — the review pane or a file tab sheet on a narrow screen
 * 5. `"drawer"` — the drawer (`drawer`)
 * 6. `"thread"` — the selected thread back to the thread list on a narrow screen
 * Returns `null` when nothing can be closed (the back button is hidden).
 */
export function backTarget(layers: BackLayers): BackTarget | null {
  const narrow = Boolean(layers.narrow);
  if (layers.outputTarget) return "output";
  if (layers.reposOpen) return "repos";
  if (layers.dialogOpen) return "dialog";
  if (narrow && Boolean(layers.sheetOpen ?? layers.tabsShown)) return "sheet";
  if (layers.drawer) return "drawer";
  if (narrow && Boolean(layers.threadSelected ?? layers.selected)) return "thread";
  return null;
}

export interface DrawerFilesProps {
  host?: bridge.Host;
  open?: boolean;
  view: "ask" | "work";
  cwd: string | null;
  client: RpcClient;
  listen: Listen;
  selected: string | null;
  onOpen: (path: string) => void;
  onClose: () => void;
}

/**
 * The drawer's "Files" section for the selected Work thread's folder on the web host (CHAT_REPOS §10 G12):
 * renders `FileTree` read-only and closes the drawer when a file is opened.
 */
export function DrawerFiles(props: DrawerFilesProps) {
  const activeHost = props.host ?? bridge.host();
  if (activeHost !== "web" || props.open === false || props.view !== "work" || !props.cwd) {
    return null;
  }
  return createElement(
    "section",
    { className: "project drawer-files", "aria-label": "Files" },
    createElement("h2", { className: "section-label" }, "Files"),
    createElement(FileTree, {
      root: props.cwd,
      client: props.client,
      listen: props.listen,
      selected: props.selected,
      onOpen: (path: string) => {
        props.onOpen(path);
        props.onClose();
      },
      onClose: props.onClose,
    }),
  );
}
