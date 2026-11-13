import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { useAuth } from "../auth/useAuth";
import {
  LOGOUT_MARKER_KEY,
  getAccessToken,
  getTender,
  isLoggingOut,
  login,
  logout,
  startSession,
} from "./client";

// Plan item 34, options 3 and 2: the API rotates the refresh token on every
// refresh, and the token is an HttpOnly cookie the browser keeps, invisible
// to the client. The client takes a cross-tab Web Lock around the refresh,
// keeps only the access token (sessionStorage), and logs out on the server.
// jsdom has no navigator.locks; the tests that need one install a small fake
// with the same contract: one holder per name at a time, the others queued in
// order.

interface Call {
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

let calls: Call[] = [];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(answer: (call: Call, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const call: Call = {
        path: new URL(input).pathname,
        headers: { ...(init?.headers as Record<string, string>) },
        body: init?.body,
      };
      calls.push(call);
      return answer(call, init);
    }),
  );
}

const callsTo = (path: string) => calls.filter((call) => call.path === path);
const EXPIRED = () => json(401, { detail: "Invalid or expired token" });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const isNewAccess = (call: Call) => /^Bearer new-access/.test(call.headers["Authorization"] ?? "");

function installFakeLocks(): { requests: string[] } {
  const tails = new Map<string, Promise<unknown>>();
  const requests: string[] = [];
  const locks = {
    request(name: string, callback: (lock: Lock | null) => unknown) {
      requests.push(name);
      const previous = tails.get(name) ?? Promise.resolve();
      const run = previous.then(() => callback({ name, mode: "exclusive" } as Lock));
      tails.set(
        name,
        run.catch(() => undefined),
      );
      return run;
    },
    query: async () => ({ held: [], pending: [] }),
  };
  Object.defineProperty(navigator, "locks", { value: locks, configurable: true });
  return { requests };
}

beforeEach(() => {
  calls = [];
  startSession("old-access");
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.resetModules();
  // Only defined by installFakeLocks; jsdom has no navigator.locks.
  delete (navigator as { locks?: unknown }).locks;
});

describe("rotation", () => {
  it("a refresh stores only the new access token; the cookie is the browser's", async () => {
    stubFetch((call) => {
      if (call.path === "/auth/refresh") {
        return json(200, { access_token: "new-access", token_type: "bearer" });
      }
      return isNewAccess(call) ? json(200, { id: call.path }) : EXPIRED();
    });

    await expect(getTender("a")).resolves.toEqual({ id: "/tenders/a" });

    expect(getAccessToken()).toBe("new-access");
    expect(callsTo("/auth/refresh")[0].body).toBeUndefined();
    expect(localStorage.length).toBe(0);
  });

  it("without navigator.locks the refresh still runs, unlocked", async () => {
    expect("locks" in navigator).toBe(false);
    stubFetch((call) =>
      call.path === "/auth/refresh"
        ? json(200, { access_token: "new-access" })
        : isNewAccess(call)
          ? json(200, { id: call.path })
          : EXPIRED(),
    );

    await expect(getTender("a")).resolves.toEqual({ id: "/tenders/a" });
    expect(callsTo("/auth/refresh")).toHaveLength(1);
  });
});

describe("two tabs", () => {
  // Each tab is its own instance of client.ts (its own single-flight
  // promise and session counter), sharing navigator.locks and localStorage,
  // as two browser tabs of one origin do. jsdom has one sessionStorage, so
  // here the two tabs share it, where a browser would give each its own;
  // every case below holds either way.
  async function twoTabs() {
    vi.resetModules();
    const tabA = await import("./client");
    vi.resetModules();
    const tabB = await import("./client");
    return { tabA, tabB };
  }

  // Each answer carries its own access token, so a test can see which tab
  // got which.
  function answerRefreshes(held?: Promise<void>) {
    stubFetch(async (call) => {
      if (call.path === "/auth/refresh") {
        if (held) await held;
        return json(200, { access_token: `new-access-${callsTo("/auth/refresh").length}` });
      }
      return isNewAccess(call) ? json(200, { id: call.path }) : EXPIRED();
    });
  }

  it("whose access tokens expire together refresh one after the other, under the lock", async () => {
    const { requests } = installFakeLocks();
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    answerRefreshes(refreshHeld);
    const { tabA, tabB } = await twoTabs();

    const both = Promise.all([tabA.getTender("a"), tabB.getTender("b")]);
    // Both tabs got their 401 and asked for the lock while the first
    // refresh is held: the second has not been sent, it waits for the lock.
    await settle();
    expect(requests).toEqual(["tenderos-refresh", "tenderos-refresh"]);
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    releaseRefresh();

    // The second tab cannot read the cookie to see the first rotated it, so
    // it refreshes too, with the rotated cookie the browser now holds.
    await expect(both).resolves.toEqual([{ id: "/tenders/a" }, { id: "/tenders/b" }]);
    expect(callsTo("/auth/refresh")).toHaveLength(2);
  });

  it("without navigator.locks, refreshing at the same moment, both stay signed in", async () => {
    // Plan item 34, option 2's condition. Both refreshes are in flight at
    // once, carrying the same cookie; the api answers both with the same
    // rotated cookie (tests/route/test_auth_routes.py proves that half).
    // This proves the client half: neither tab drops its answer or signs
    // out.
    expect("locks" in navigator).toBe(false);
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    answerRefreshes(refreshHeld);
    const { tabA, tabB } = await twoTabs();
    const expired = vi.fn();
    tabA.setSessionExpiredHandler(expired);
    tabB.setSessionExpiredHandler(expired);

    const both = Promise.all([tabA.getTender("a"), tabB.getTender("b")]);
    await settle();
    expect(callsTo("/auth/refresh")).toHaveLength(2);
    releaseRefresh();

    await expect(both).resolves.toEqual([{ id: "/tenders/a" }, { id: "/tenders/b" }]);
    expect(expired).not.toHaveBeenCalled();
  });

  it("a tab that waited while the other tab's refresh was rejected is rejected too, and lands on login", async () => {
    installFakeLocks();
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    // Tab A's refresh is rejected (its family was revoked), and the api's 401
    // cleared the cookie, so tab B's own refresh, sent once it has the lock,
    // carries no cookie and is rejected as well.
    stubFetch(async (call) => {
      if (call.path === "/auth/refresh") {
        await refreshHeld;
        return EXPIRED();
      }
      return EXPIRED();
    });
    const { tabA, tabB } = await twoTabs();
    const expiredA = vi.fn();
    const expiredB = vi.fn();
    tabA.setSessionExpiredHandler(expiredA);
    tabB.setSessionExpiredHandler(expiredB);

    const both = Promise.allSettled([tabA.getTender("a"), tabB.getTender("b")]);
    await settle();
    releaseRefresh();
    await both;

    expect(callsTo("/auth/refresh")).toHaveLength(2);
    expect(expiredA).toHaveBeenCalledTimes(1);
    expect(expiredB).toHaveBeenCalledTimes(1);
  });

  it("a tab waiting on the lock while another tab logs out sends no refresh and lands on login", async () => {
    installFakeLocks();
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    answerRefreshes(refreshHeld);
    const { tabA, tabB } = await twoTabs();
    const expiredB = vi.fn();
    tabB.setSessionExpiredHandler(expiredB);

    const both = Promise.allSettled([tabA.getTender("a"), tabB.getTender("b")]);
    await settle();
    // A third tab logs out while tab A's refresh holds the lock.
    localStorage.setItem(LOGOUT_MARKER_KEY, "now");
    releaseRefresh();
    await both;

    expect(callsTo("/auth/refresh")).toHaveLength(1);
    expect(expiredB).toHaveBeenCalledTimes(1);
  });

  it("a refresh that hangs is aborted after its timeout, and the waiting tab then refreshes", async () => {
    installFakeLocks();
    stubFetch((call, init) => {
      if (call.path === "/auth/refresh") {
        if (callsTo("/auth/refresh").length === 1) {
          // The first refresh never answers; only its abort ends it.
          return new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          });
        }
        return json(200, { access_token: "new-access" });
      }
      return isNewAccess(call) ? json(200, { id: call.path }) : EXPIRED();
    });
    const { tabA, tabB } = await twoTabs();
    const expired = vi.fn();
    tabA.setSessionExpiredHandler(expired);
    tabB.setSessionExpiredHandler(expired);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });

    const both = Promise.allSettled([tabA.getTender("a"), tabB.getTender("b")]);
    // One tab holds the lock with a hung refresh; the other waits on it.
    await vi.advanceTimersByTimeAsync(9_999);
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    const results = await both;

    // The hung tab's request fails like a network failure (its original
    // 401, session kept); the waiting tab then took the lock and refreshed.
    expect(callsTo("/auth/refresh")).toHaveLength(2);
    const fulfilled = results.filter((r) => r.status === "fulfilled");
    const rejected = results.filter((r) => r.status === "rejected");
    expect(fulfilled).toHaveLength(1);
    expect(rejected).toHaveLength(1);
    expect((rejected[0] as PromiseRejectedResult).reason.status).toBe(401);
    expect(expired).not.toHaveBeenCalled();
  });
});

describe("logout", () => {
  it("sets the marker and clears the access token before the call, then clears the marker on 204", async () => {
    let markerDuringCall: string | null = "unset";
    let accessDuringCall: string | null = "unset";
    let sent: RequestInit | undefined;
    stubFetch((_call, init) => {
      markerDuringCall = localStorage.getItem(LOGOUT_MARKER_KEY);
      accessDuringCall = getAccessToken();
      sent = init;
      return new Response(null, { status: 204 });
    });

    await logout();

    expect(callsTo("/auth/logout")).toHaveLength(1);
    // Cleared before the call, not after (plan item 39's review): a tab
    // closed during the wait of up to 5 s stays signed out.
    expect(markerDuringCall).not.toBeNull();
    expect(accessDuringCall).toBeNull();
    const [call] = callsTo("/auth/logout");
    expect(call.body).toBeUndefined();
    expect(call.headers["X-TenderOS-Client"]).toBe("web");
    expect(call.headers["Authorization"]).toBeUndefined();
    expect(sent?.credentials).toBe("same-origin");
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).toBeNull();
  });

  it.each<[string, () => Response]>([
    [
      "cannot reach the API",
      () => {
        throw new TypeError("Failed to fetch");
      },
    ],
    ["answers with an error", () => json(503, { detail: "unavailable" })],
  ])("keeps the marker, and the tab signed out, when the call %s", async (_name, answer) => {
    stubFetch(() => answer());

    await expect(logout()).resolves.toBeUndefined();

    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).not.toBeNull();
  });

  it("always makes the call: the cookie is invisible to the client", async () => {
    sessionStorage.clear();
    stubFetch(() => new Response(null, { status: 204 }));

    await logout();

    expect(callsTo("/auth/logout")).toHaveLength(1);
  });

  it("sends the call with keepalive, so it can outlive a closed tab", async () => {
    let sent: RequestInit | undefined;
    stubFetch((_call, init) => {
      sent = init;
      return new Response(null, { status: 204 });
    });

    await logout();

    expect(sent?.keepalive).toBe(true);
    expect(sent?.signal).toBeInstanceOf(AbortSignal);
  });

  it.each<[string, () => Response]>([
    ["works", () => new Response(null, { status: 204 })],
    ["answers with an error", () => json(503, { detail: "unavailable" })],
    [
      "cannot reach the API",
      () => {
        throw new TypeError("Failed to fetch");
      },
    ],
  ])("is logging out only while the call runs, when the call %s", async (_name, answer) => {
    let duringCall: boolean | null = null;
    stubFetch(() => {
      duringCall = isLoggingOut();
      return answer();
    });
    expect(isLoggingOut()).toBe(false);

    await logout();

    expect(duringCall).toBe(true);
    expect(isLoggingOut()).toBe(false);
  });

  it("still ends the session when the call hangs, ending within its 5 s bound", async () => {
    stubFetch(
      (_call, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal!.reason));
        }),
    );

    const started = Date.now();
    await logout();

    expect(Date.now() - started).toBeGreaterThan(4_500);
    expect(Date.now() - started).toBeLessThan(6_500);
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).not.toBeNull();
    expect(isLoggingOut()).toBe(false);
  }, 10_000);

  it("a login waits for a log out call still out, so that answer cannot clear the new cookie", async () => {
    let answerLogout!: (response: Response) => void;
    stubFetch((call) => {
      if (call.path === "/auth/logout") {
        return new Promise<Response>((resolve) => (answerLogout = resolve));
      }
      return json(200, { access_token: "login-access" });
    });

    const loggingOut = logout();
    const loggingIn = login({ email: "a@example.com", password: "x" });
    await settle();
    expect(callsTo("/auth/login")).toHaveLength(0);

    answerLogout(new Response(null, { status: 204 }));
    await loggingOut;
    await expect(loggingIn).resolves.toEqual({ access_token: "login-access" });
    expect(calls.map((call) => call.path)).toEqual(["/auth/logout", "/auth/login"]);
  });
});

// The real AuthProvider: logout() from the auth context revokes on the
// server and then ends the session on screen.
function SessionProbe() {
  const { meResponse, logout: signOut } = useAuth();
  const state = meResponse === undefined ? "loading" : meResponse ? "signed in" : "signed out";
  return (
    <p>
      {state}
      <button type="button" onClick={() => void signOut()}>
        sign out
      </button>
    </p>
  );
}

describe("the auth context's logout", () => {
  it("calls /auth/logout, clears the token and the marker, and ends the session", async () => {
    stubFetch((call) => {
      if (call.path === "/auth/me") {
        return json(200, {
          user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
          memberships: [{ organization_id: "org-1", role: "admin" }],
        });
      }
      return new Response(null, { status: 204 });
    });
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <SessionProbe />
      </AuthProvider>,
    );
    expect(await screen.findByText("signed in")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "sign out" }));

    expect(await screen.findByText("signed out")).toBeInTheDocument();
    expect(callsTo("/auth/logout")).toHaveLength(1);
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).toBeNull();
  });
});
