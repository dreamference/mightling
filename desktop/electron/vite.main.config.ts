// The main process: `src/early-bootstrap.ts` and what it imports, bundled to `.vite/build/`.
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    sourcemap: false,
    rollupOptions: {
      // Electron and Node's modules stay external; multicast-dns is bundled in.
      external: ["electron"],
    },
  },
});
