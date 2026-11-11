import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { configDefaults } from "vitest/config";

// Plan item 27: the dev server answers /api/ the way production's nginx does,
// by proxying to the api with the prefix removed, so a build with
// VITE_API_BASE_URL=/api works under `npm run dev` too. The target is the api
// on the host by default; docker-compose.dev.yml sets it to the api service.
// SSE streams pass through the proxy unbuffered.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";
// File-change events from a Windows host do not reach a container through a
// bind mount, so docker-compose.dev.yml asks the watcher to poll instead.
const watchPolling = process.env.VITE_WATCH_POLLING === "1";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    watch: watchPolling ? { usePolling: true, interval: 300 } : undefined,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    // Part A2's locked frontend testing tool - jsdom, not the default
    // node environment, since these tests render real components.
    // globals intentionally off - test files import describe/it/expect
    // explicitly from "vitest", keeping tsc -b's own noUnusedLocals/
    // strict checking meaningful for them too, not just production code.
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // Plan item 27 (Node 26): from Node 25, Node defines its own global
    // localStorage, undefined unless --localstorage-file is given, and it
    // shadows jsdom's; every test that stores a token then failed
    // (observed: 37 of 414 under Node 26.13.0). Turning Node's Web Storage
    // off in the test workers leaves jsdom's, as under Node 22. The app
    // itself runs in a browser and is unaffected.
    execArgv: ["--no-experimental-webstorage"],
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
