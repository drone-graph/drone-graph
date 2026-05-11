import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

// Local-only mission control. Vite serves the dev frontend at :5173 and
// proxies API calls to the FastAPI server at :8765 (so SSE works without
// CORS gymnastics). The production build is served straight from FastAPI.
export default defineConfig({
  plugins: [solid()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        // Long-lived SSE connection — disable buffering.
        ws: false,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    target: "es2022",
  },
});
