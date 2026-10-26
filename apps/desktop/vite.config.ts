/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 1420, strictPort: true },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"], globals: true },
});
