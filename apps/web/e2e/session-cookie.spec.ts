import { expect, test } from "@playwright/test";
import type { BrowserContext, Page } from "@playwright/test";

// Plan item 34, option 2: the refresh token in an HttpOnly cookie. A small
// spec of its own, beside the frozen full-flow.spec.ts, against the same
// stack (the production nginx build, the api behind /api). Four checks, each
// on the real browser's cookie jar:
//   1. still signed in after a reload;
//   2. the cookie is not readable from page script;
//   3. a refresh without the X-TenderOS-Client header is refused, and leaves
//      the session working;
//   4. Log out clears the cookie, and a reload stays signed out.

const COOKIE = "tenderos_refresh";
const LOG_OUT = /^Log out/;

async function refreshCookie(context: BrowserContext) {
  return (await context.cookies()).find((cookie) => cookie.name === COOKIE);
}

async function registerThroughTheForm(page: Page) {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create organization" }).click();
  await page.getByLabel("Email").fill(`cookie-${suffix}@example.com`);
  await page.getByLabel("Password").fill(`pw-${suffix}-${suffix}`);
  await page.getByLabel("Organization name").fill(`Cookie Org ${suffix}`);
  await page.locator("form").getByRole("button", { name: "Create organization" }).click();
  await expect(page.getByRole("button", { name: LOG_OUT })).toBeVisible();
}

test("the refresh cookie: reload, script access, the header check, log out", async ({
  page,
  context,
}) => {
  await registerThroughTheForm(page);

  // The cookie as the browser stored it.
  const cookie = await refreshCookie(context);
  expect(cookie, "register set the refresh cookie").toBeDefined();
  expect(cookie!.httpOnly).toBe(true);
  expect(cookie!.secure).toBe(true);
  expect(cookie!.sameSite).toBe("Strict");
  expect(cookie!.path).toBe("/api/auth");
  // No token in localStorage; the access token in this tab's sessionStorage.
  const storage = await page.evaluate(() => ({
    local: Object.keys(localStorage),
    session: sessionStorage.getItem("tenderos_access_token") !== null,
  }));
  expect(storage.local.filter((key) => key.includes("token"))).toEqual([]);
  expect(storage.session).toBe(true);

  // 1. Still signed in after a reload.
  await page.reload();
  await expect(page.getByRole("button", { name: LOG_OUT })).toBeVisible();
  expect(new URL(page.url()).pathname).not.toBe("/login");

  // 2. Not readable from script.
  const scriptSees = await page.evaluate(() => document.cookie);
  expect(scriptSees).not.toContain(COOKIE);

  // 3. A refresh without the header is refused. The browser attaches the
  // cookie (same origin, its path), so only the header check stops it.
  const refused = await page.evaluate(async () => {
    const response = await fetch("/api/auth/refresh", { method: "POST" });
    return { status: response.status, body: await response.json() };
  });
  expect(refused.status).toBe(403);
  expect(refused.body.error.code).toBe("CSRF_CHECK_FAILED");
  // The refused call spent nothing: with this tab's access token gone, a
  // reload signs in again from the same cookie.
  expect((await refreshCookie(context))?.value).toBe(cookie!.value);
  await page.evaluate(() => sessionStorage.clear());
  const restored = page.waitForResponse(
    (response) => response.url().endsWith("/api/auth/refresh") && response.status() === 200,
  );
  await page.reload();
  await restored;
  await expect(page.getByRole("button", { name: LOG_OUT })).toBeVisible();

  // 4. Log out clears the cookie.
  const loggedOut = page.waitForResponse(
    (response) => response.url().endsWith("/api/auth/logout") && response.status() === 204,
  );
  await page.getByRole("button", { name: LOG_OUT }).click();
  await loggedOut;
  await expect(page.getByRole("heading", { name: "TenderOS" })).toBeVisible();
  expect(await refreshCookie(context)).toBeUndefined();
  const marker = await page.evaluate(() => localStorage.getItem("tenderos_logged_out"));
  expect(marker).toBeNull();
  await page.reload();
  await expect(page.getByRole("heading", { name: "TenderOS" })).toBeVisible();
  await expect(page.getByRole("button", { name: LOG_OUT })).toHaveCount(0);
});
