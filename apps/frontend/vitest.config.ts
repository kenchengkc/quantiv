import { configDefaults, defineConfig } from "vitest/config";
import { resolve } from "path";

export default defineConfig({
  esbuild: {
    jsx: "automatic",
  },
  test: {
    environment: "jsdom",
    exclude: [...configDefaults.exclude, "e2e/**"],
    globals: true,
    passWithNoTests: true,
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "./"),
      "@/lib": resolve(__dirname, "./lib"),
      "@/app": resolve(__dirname, "./app"),
    },
  },
});
