// Based on AMN Production OS vite.config.ts; proxies the Flask engine (studio serve) in dev.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 3000, proxy: { "/api": "http://127.0.0.1:8765", "/file": "http://127.0.0.1:8765" } },
});
