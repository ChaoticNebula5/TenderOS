import { useEffect, useState } from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { RequireAuth } from "../auth/RequireAuth";
import { Login } from "../routes/Login";
import {
  ApiError,
  LOGOUT_MARKER_KEY,
  createWorkflowRun,
  endSessionInThisTab,
  getAccessToken,
  getTender,
  login,
  logout,
  mintWorkflowEventsTicket,
  setSessionExpiredHandler,
  startSession,
} from "./client";

// Phase 4 plan item 34: client.ts refreshes an expired access token once,
// shared by every request that got a 401 meanwhile, and retries each
// request once. These drive the real client against a stubbed fetch that
// answers by path and by the bearer token sent, so each test can count
// the refresh calls and read the headers of every attempt.
//
// Option 2: the refresh token is an HttpOnly cookie the browser sends and
// replaces on its own, invisible here as it is to any script. A refresh is
// a POST with no body; only the access token is stored, in sessionStorage.

interface Call {
  path: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
  credentials: RequestCredentials | undefined;
}

let calls: Call[] = [];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(answer: (call: Call) => Response | Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const call: Call = {
        path: new URL(input).pathname,
        method: init?.method ?? "GET",
        headers: { ...(init?.headers as Record<string, string>) },
        body: init?.body,
        credentials: init?.credentials,
      };
      calls.push(call);
      return answer(call);
    }),
  );
}

const callsTo = (path: string) => calls.filter((call) => call.path === path);
const EXPIRED = json(401, { detail: "Invalid or expired token" });
const REFRESHED = () => json(200, { access_token: "new-access", token_type: "bearer" });
// A macrotask: every promise continuation queued so far has run.
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  calls = [];
  startSession("old-access");
});

afterEach(() => {
  cleanup();
  setSessionExpiredHandler(null);
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("refresh on a 401", () => {
  it("two parallel 401s share exactly one refresh, and both requests succeed", async () => {
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    stubFetch(async (call) => {
      if (call.path === "/auth/refresh") {
        await refreshHeld;
        return REFRESHED();
      }
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED.clone();
    });

    const both = Promise.all([getTender("a"), getTender("b")]);
    // Both 401s have arrived and are waiting while the refresh is held.
    await settle();
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    releaseRefresh();

    await expect(both).resolves.toEqual([{ id: "/tenders/a" }, { id: "/tenders/b" }]);
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    expect(getAccessToken()).toBe("new-access");
  });

  it("a refresh is a POST with the client header, the cookie's credentials and no body", async () => {
    stubFetch((call) => {
      if (call.path === "/auth/refresh") return REFRESHED();
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED.clone();
    });

    await getTender("a");

    const [refresh] = callsTo("/auth/refresh");
    expect(refresh.method).toBe("POST");
    expect(refresh.headers["X-TenderOS-Client"]).toBe("web");
    expect(refresh.credentials).toBe("same-origin");
    expect(refresh.body).toBeUndefined();
    expect(refresh.headers["Authorization"]).toBeUndefined();
  });

  it("a 401 that arrives after the refresh finished retries with the new token, no second refresh", async () => {
    let releaseB!: () => void;
    const bHeld = new Promise<void>((resolve) => (releaseB = resolve));
    stubFetch(async (call) => {
      if (call.path === "/auth/refresh") return REFRESHED();
      if (call.headers["Authorization"] === "Bearer new-access")
        return json(200, { id: call.path });
      // b's first attempt is answered only after a has refreshed and retried.
      if (call.path === "/tenders/b") await bHeld;
      return EXPIRED.clone();
    });

    const b = getTender("b");
    await expect(getTender("a")).resolves.toEqual({ id: "/tenders/a" });
    releaseB();

    await expect(b).resolves.toEqual({ id: "/tenders/b" });
    expect(callsTo("/auth/refresh")).toHaveLength(1);
  });

  it("a 401 after a successful refresh logs out, with no second refresh and no loop", async () => {
    const expired = vi.fn();
    setSessionExpiredHandler(expired);
    stubFetch((call) => (call.path === "/auth/refresh" ? REFRESHED() : EXPIRED.clone()));

    const error = await getTender("a").catch((err: unknown) => err);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    expect(callsTo("/tenders/a")).toHaveLength(2);
    expect(getAccessToken()).toBeNull();
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("an idempotent POST is retried with the same Idempotency-Key and the new token", async () => {
    stubFetch((call) => {
      if (call.path === "/auth/refresh") return REFRESHED();
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(202, { id: "run-1" })
        : EXPIRED.clone();
    });

    await expect(createWorkflowRun("t1", "key-123")).resolves.toEqual({ id: "run-1" });

    const attempts = callsTo("/tenders/t1/workflow-runs");
    expect(attempts).toHaveLength(2);
    expect(attempts.map((call) => call.method)).toEqual(["POST", "POST"]);
    expect(attempts.map((call) => call.headers["Idempotency-Key"])).toEqual(["key-123", "key-123"]);
    expect(attempts.map((call) => call.headers["Authorization"])).toEqual([
      "Bearer old-access",
      "Bearer new-access",
    ]);
  });

  it("an SSE ticket mint that gets a 401 goes through the same refresh", async () => {
    stubFetch((call) => {
      if (call.path === "/auth/refresh") return REFRESHED();
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { ticket: "t-1", expires_in_seconds: 30 })
        : EXPIRED.clone();
    });

    await expect(mintWorkflowEventsTicket("run-1")).resolves.toEqual({
      ticket: "t-1",
      expires_in_seconds: 30,
    });
    expect(callsTo("/auth/refresh")).toHaveLength(1);
  });

  it("a refresh that cannot reach the API keeps the session and returns the original 401", async () => {
    const expired = vi.fn();
    setSessionExpiredHandler(expired);
    stubFetch((call) => {
      if (call.path === "/auth/refresh") throw new TypeError("Failed to fetch");
      return EXPIRED.clone();
    });

    const error = await getTender("a").catch((err: unknown) => err);

    expect((error as ApiError).status).toBe(401);
    expect(callsTo("/tenders/a")).toHaveLength(1);
    expect(getAccessToken()).toBe("old-access");
    expect(expired).not.toHaveBeenCalled();
  });

  it("a rate-limited refresh (429) keeps the session and returns the original 401", async () => {
    // A 429 is transient; behind a proxy every user can share one IP
    // (Phase 4 blocker 1, limit 3), so it must not log anyone out.
    const expired = vi.fn();
    setSessionExpiredHandler(expired);
    stubFetch((call) =>
      call.path === "/auth/refresh"
        ? json(429, { detail: "Rate limit exceeded" })
        : EXPIRED.clone(),
    );

    const error = await getTender("a").catch((err: unknown) => err);

    expect((error as ApiError).status).toBe(401);
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    expect(callsTo("/tenders/a")).toHaveLength(1);
    expect(getAccessToken()).toBe("old-access");
    expect(expired).not.toHaveBeenCalled();
  });

  it("a log out in this tab while a refresh is in flight stays logged out", async () => {
    // Found in option 3's live check: the tokens were cleared while a page
    // load's refresh was in flight, and its answer wrote a new access token
    // into the emptied storage, so the next load was signed in again.
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    stubFetch(async (call) => {
      if (call.path === "/auth/logout") return new Response(null, { status: 204 });
      if (call.path === "/auth/refresh") {
        await refreshHeld;
        return REFRESHED();
      }
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED.clone();
    });

    const request = getTender("a").catch((err: unknown) => err);
    await settle();
    await logout();
    releaseRefresh();

    expect(((await request) as ApiError).status).toBe(401);
    expect(getAccessToken()).toBeNull();
    expect(callsTo("/tenders/a")).toHaveLength(1);
  });

  it.each<[string, () => void]>([
    ["sets the log out marker", () => localStorage.setItem(LOGOUT_MARKER_KEY, "now")],
    ["signs this tab out", () => endSessionInThisTab()],
  ])(
    "a refresh answer is dropped when another tab %s while it is in flight",
    async (_name, otherTab) => {
      let releaseRefresh!: () => void;
      const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
      stubFetch(async (call) => {
        if (call.path === "/auth/refresh") {
          await refreshHeld;
          return REFRESHED();
        }
        return call.headers["Authorization"] === "Bearer new-access"
          ? json(200, { id: call.path })
          : EXPIRED.clone();
      });

      const request = getTender("a").catch((err: unknown) => err);
      await settle();
      otherTab();
      releaseRefresh();

      expect(((await request) as ApiError).status).toBe(401);
      expect(getAccessToken()).not.toBe("new-access");
      expect(callsTo("/tenders/a")).toHaveLength(1);
    },
  );

  it("a 401 from login itself is a wrong password: no refresh", async () => {
    stubFetch(() => json(401, { detail: "Invalid credentials" }));

    await expect(login({ email: "a@example.com", password: "x" })).rejects.toBeInstanceOf(ApiError);
    expect(callsTo("/auth/refresh")).toHaveLength(0);
    expect(callsTo("/auth/login")[0].headers["X-TenderOS-Client"]).toBe("web");
  });
});

describe("where the tokens are kept", () => {
  it("no code path writes a token to localStorage: only the log out marker", async () => {
    const writes: [Storage, string][] = [];
    const original = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      writes.push([this, key]);
      original.call(this, key, value);
    });
    stubFetch((call) => {
      if (call.path === "/auth/login") return json(200, { access_token: "login-access" });
      if (call.path === "/auth/refresh") return REFRESHED();
      if (call.path === "/auth/logout") return new Response(null, { status: 204 });
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED.clone();
    });

    startSession((await login({ email: "a@example.com", password: "x" })).access_token);
    sessionStorage.setItem("tenderos_access_token", "expired-access");
    await getTender("a");
    await logout();

    const toLocal = writes.filter(([storage]) => storage === localStorage).map(([, key]) => key);
    expect(toLocal).toEqual([LOGOUT_MARKER_KEY]);
    expect(writes.some(([storage]) => storage === sessionStorage)).toBe(true);
  });

  it("the access token is in sessionStorage, and the old localStorage keys are deleted", () => {
    localStorage.setItem("tenderos_access_token", "legacy-access");
    localStorage.setItem("tenderos_refresh_token", "legacy-refresh");

    startSession("this-tab");

    expect(sessionStorage.getItem("tenderos_access_token")).toBe("this-tab");
    expect(localStorage.getItem("tenderos_access_token")).toBeNull();
    expect(localStorage.getItem("tenderos_refresh_token")).toBeNull();
  });

  it("a new session clears a log out marker left by a call that failed, and can refresh", async () => {
    localStorage.setItem(LOGOUT_MARKER_KEY, "2026-11-13T10:00:00Z");
    stubFetch((call) => {
      if (call.path === "/auth/refresh") return REFRESHED();
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED.clone();
    });

    startSession("signed-in-again");
    sessionStorage.setItem("tenderos_access_token", "expired-access");

    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).toBeNull();
    await expect(getTender("a")).resolves.toEqual({ id: "/tenders/a" });
    expect(callsTo("/auth/refresh")).toHaveLength(1);
  });
});

const MEMBER = {
  user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
  memberships: [{ organization_id: "org-1", role: "admin" }],
};

function SessionProbe() {
  const location = useLocation();
  return <p>{`signed in at ${location.pathname}`}</p>;
}

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <SessionProbe />
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("start-up with no access token in this tab", () => {
  beforeEach(() => sessionStorage.clear());

  it("refreshes with the cookie first, then asks /auth/me with the new token", async () => {
    stubFetch((call) => {
      if (call.path === "/auth/refresh") return REFRESHED();
      if (call.path === "/auth/me") {
        return call.headers["Authorization"] === "Bearer new-access"
          ? json(200, MEMBER)
          : EXPIRED.clone();
      }
      return EXPIRED.clone();
    });

    renderApp("/tenders/t1");

    expect(await screen.findByText("signed in at /tenders/t1")).toBeInTheDocument();
    expect(calls.map((call) => call.path)).toEqual(["/auth/refresh", "/auth/me"]);
    expect(getAccessToken()).toBe("new-access");
  });

  it("with no live cookie (the refresh is 401) shows the login screen without asking /auth/me", async () => {
    stubFetch(() => EXPIRED.clone());

    renderApp("/tenders/t1");

    expect(await screen.findByRole("heading", { name: "TenderOS" })).toBeInTheDocument();
    expect(calls.map((call) => call.path)).toEqual(["/auth/refresh"]);
    expect(getAccessToken()).toBeNull();
  });
});

// The real AuthProvider, RequireAuth and Login: a screen's request gets a
// 401, the refresh is rejected, and the user must land on the login screen
// and, after logging in again, back on the page they were on.
function TenderPage() {
  const location = useLocation();
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    getTender("t1").then(
      (tender) => setName(tender.name),
      () => undefined,
    );
  }, []);
  return <p>{name === null ? "loading tender" : `${name} ${location.search}${location.hash}`}</p>;
}

describe("a refresh the API rejects", () => {
  it("lands on the login screen, and logging in returns to the same page", async () => {
    stubFetch((call) => {
      const bearer = call.headers["Authorization"];
      if (call.path === "/auth/me") return json(200, MEMBER);
      if (call.path === "/auth/refresh") return EXPIRED.clone();
      if (call.path === "/auth/login") {
        return json(200, { access_token: "fresh-access", token_type: "bearer" });
      }
      return bearer === "Bearer fresh-access" ? json(200, { name: "Tender One" }) : EXPIRED.clone();
    });
    const user = userEvent.setup();

    const { container } = render(
      <MemoryRouter initialEntries={["/tenders/t1/compliance?tab=findings#row-7"]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/tenders/:tenderId/compliance"
              element={
                <RequireAuth>
                  <TenderPage />
                </RequireAuth>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "TenderOS" })).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
    expect(callsTo("/auth/refresh")).toHaveLength(1);

    const form = within(container.querySelector("form")!);
    await user.type(form.getByLabelText("Email"), "person@example.com");
    await user.type(form.getByLabelText("Password"), "a-password");
    await user.click(form.getByRole("button", { name: "Log in" }));

    expect(await screen.findByText("Tender One ?tab=findings#row-7")).toBeInTheDocument();
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    expect(getAccessToken()).toBe("fresh-access");
  });
});
