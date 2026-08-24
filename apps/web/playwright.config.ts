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
// `docker compose up -d --build api web worker` must already be running
// and healthy before `npm run test:e2e` - this config does not start it.
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
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
