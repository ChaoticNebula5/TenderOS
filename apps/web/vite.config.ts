/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    // Part A2's locked frontend testing tool - jsdom, not the default
    // node environment, since these tests render real components.
    // globals intentionally off - test files import describe/it/expect
    // explicitly from "vitest", keeping tsc -b's own noUnusedLocals/
    // strict checking meaningful for them too, not just production code.
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
