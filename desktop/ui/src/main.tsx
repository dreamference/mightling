import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { host } from "./bridge";
import "./styles.css";

// Styles that belong to one host (the desktop app's frameless title bar) key on this.
document.documentElement.dataset.host = host();

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
