import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server config. The API origin defaults to http://127.0.0.1:8000
// (set RTIA_API_HOST / RTIA_API_PORT in .env to match run_api.py) and is
// proxied for /pipeline, /uploads, and /legacy so the browser sees a
// same-origin app and the bearer cookie carries through.
const API_ORIGIN =
  process.env.RTIA_API_ORIGIN ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/pipeline": { target: API_ORIGIN, changeOrigin: true },
      "/uploads": { target: API_ORIGIN, changeOrigin: true },
      "/legacy": { target: API_ORIGIN, changeOrigin: true, ws: true },
    },
  },
});
