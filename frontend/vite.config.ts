import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/app/",
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8822",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8822",
        ws: true,
      },
    },
  },
  build: {
    outDir: "../sshler/static/dist",
    emptyOutDir: true,
  },
});
