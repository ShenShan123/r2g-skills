import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build goes straight into the Python package, so `pip install` ships the UI
// and users never need Node.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../agent/r2g_agent/web/dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8770" } },
});
