import { defineConfig } from "@playwright/test";

// §7 step 11a (B13 plan). No `webServer` block, deliberately - that's
// Playwright's own "start a dev server for me" feature, and using it
// would mean testing against `vite dev`, not the real `web`/`api`/`worker`
// containers. This is the one config choice that actually enforces "real
// Docker stack, not a mocked/dev network layer" (Part J's own gate), so
// it's a stated omission, not an unnoticed default. Run from the host,
// not a container - no dedicated test-runner service exists in
// docker-compose.yml, and this project's own established pattern all
// through B13 has been host-side tooling driving the containers through
// their published ports (curl, psql via `docker compose exec`, the
// Playwright MCP browser this session used for every live-browser check).
// B16: `docker compose -f docker-compose.yml -f docker-compose.e2e.yml up
// -d --build api web worker outbox_dispatcher` - the second file adds
// E2E_RATE_LIMIT_BYPASS_ENABLED, which this config's own extraHTTPHeaders
// below depends on to actually do anything (see main.py's _rate_limit
// middleware). The base-file-only command from before B16 still starts a
// working stack, just one where this spec's own large sequential-mutation
// loops (dozens of real PATCHes with no artificial delay) can trip B15's
// rate limiter - confirmed live, more than once.
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: {
    timeout: 15_000,
  },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // B16: matches docker-compose.e2e.yml's own E2E_RATE_LIMIT_BYPASS_ENABLED
    // and main.py's _E2E_BYPASS_HEADER/_E2E_BYPASS_VALUE exactly. Only this
    // file (Playwright's own test config) ever sends this header - the real
    // React app never does, by construction: it isn't in that codebase at
    // all, not even behind a flag. Applies to every request this browser
    // context makes, CORS preflight OPTIONS included - confirmed necessary,
    // not assumed: a live check found this app's own rate-limit middleware
    // does see and block preflights, not just the "real" requests that
    // follow them.
    extraHTTPHeaders: {
      "X-TenderOS-E2E-Bypass": "playwright",
    },
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
