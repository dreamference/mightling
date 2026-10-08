// Work's UI (desktop/ui, React) built into `.vite/renderer/main_window/`, which the `app://` scheme
// serves. Relative asset paths, so the bundle works under `app://-/`.
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  root: path.join(__dirname, "..", "ui"),
  plugins: [react()],
  base: "./",
  build: {
    target: "es2022",
    sourcemap: false,
    emptyOutDir: true,
    // Absolute: plugin-vite's default is relative to the root, which is desktop/ui here, and the
    // main process serves `.vite/renderer/main_window` beside its own build.
    outDir: path.join(__dirname, ".vite", "renderer", "main_window"),
  },
});
