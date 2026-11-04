import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, Vite proxies the API (FastAPI on :8000). In production FastAPI serves dist/ itself.
const API = process.env.FAIL_CLOSED_API ?? "http://127.0.0.1:8000";
const apiPaths = ["/health", "/status", "/check", "/withdraw", "/history", "/breaker", "/stream",
  "/events", "/scenarios", "/pending", "/redteam", "/loadtest", "/demo", "/guard", "/policies"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(apiPaths.map((p) => [p, { target: API, changeOrigin: false }])),
  },
  build: { outDir: "dist", sourcemap: false },
});
