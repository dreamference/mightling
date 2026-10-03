/// <reference types="vitest/config" />
// The Work window is bundled into puffin-app (tauri.conf.json `frontendDist`), never served from a
// network: everything it loads is in dist/, as the window's CSP requires.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, target: "es2022", sourcemap: false },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
