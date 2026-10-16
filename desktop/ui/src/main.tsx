import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { host } from "./bridge";
import "./styles.css";

// Styles that belong to one host (the desktop app's frameless title bar) key on this.
document.documentElement.dataset.host = host();

// Light only (DESKTOP §14 question 3, 2026-10-03; dark not approved 2026-10-16): the system theme
// is not followed. html[data-theme] stays the hook a dark block over styles.css's tokens would use.
document.documentElement.dataset.theme = "light";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
