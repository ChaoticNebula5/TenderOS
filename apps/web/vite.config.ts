import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { configDefaults } from "vitest/config";

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
    // e2e/full-flow.spec.ts (B13 §7 step 11a) matches Vitest's own
    // default *.spec.ts include glob, but it's a Playwright test -
    // test() there throws "did not expect test() to be called here"
    // under Vitest's runner, since @playwright/test's own test object
    // requires its own test runner, not Vitest's. Spreading
    // configDefaults.exclude rather than replacing it keeps Vitest's
    // own node_modules/dist/etc. exclusions intact.
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
