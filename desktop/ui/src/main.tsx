import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { PetView } from "./PetView";
import { QuickView } from "./QuickView";
import { host } from "./bridge";
import { STORAGE_KEY } from "./prefs";
import { browserThemeHost, startTheme } from "./theme";
import "./styles.css";

// Styles that belong to one host (the desktop app's frameless title bar) key on this.
document.documentElement.dataset.host = host();

// The theme (theme.ts): set before the first render, then kept on the system's or the user's choice.
startTheme(browserThemeHost(host(), STORAGE_KEY));

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      {location.hash === "#pet" ? <PetView /> : location.hash === "#quick" ? <QuickView /> : <App />}
    </StrictMode>,
  );
}
