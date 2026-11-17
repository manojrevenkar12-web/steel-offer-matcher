import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwind()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/testing/setup.ts"],
  },
});
