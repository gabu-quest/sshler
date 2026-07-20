import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: "jsdom",
    root: fileURLToPath(new URL("./", import.meta.url)),
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    resolveSnapshotPath: undefined,
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
