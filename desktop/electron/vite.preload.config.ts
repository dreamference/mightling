// The app window's preload, bundled to `.vite/build/preload.cjs`: the only code in the renderer
// that sees Electron's IPC, through `contextBridge` (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3).
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    sourcemap: false,
    rollupOptions: { external: ["electron"] },
  },
});
