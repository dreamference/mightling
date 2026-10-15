import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { host } from "./bridge";
import "./styles.css";

// Styles that belong to one host (the desktop app's frameless title bar) key on this.
document.documentElement.dataset.host = host();

// The theme follows the system's, live: styles.css keys its dark tokens on html[data-theme].
// In a browser (`ling web`) this query is the system theme. In the desktop app the preload sets it
// before the first paint and the main process also sends each change over the bridge (App.tsx),
// since Electron on Linux does not always pass a nativeTheme change on to the query.
const dark = window.matchMedia?.("(prefers-color-scheme: dark)");
const applyTheme = () => {
  document.documentElement.dataset.theme = dark?.matches ? "dark" : "light";
};
applyTheme();
dark?.addEventListener?.("change", applyTheme);

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
