import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { useAuth } from "../auth/useAuth";
import {
  getAccessToken,
  getRefreshToken,
  getTender,
  isLoggingOut,
  logout,
  setTokens,
} from "./client";

// Plan item 34, option 3: the API rotates the refresh token on every
// refresh, so the client stores both tokens, takes a cross-tab Web Lock
// around the refresh, and logs out on the server. jsdom has no
// navigator.locks; the tests that need one install a small fake with the
// same contract: one holder per name at a time, the others queued in order.

interface Call {
  path: string;
  headers: Record<string, string>;
  body: string | undefined;
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
        body: init?.body as string | undefined,
      };
      calls.push(call);
      return answer(call, init);
    }),
  );
}

const callsTo = (path: string) => calls.filter((call) => call.path === path);
const EXPIRED = () => json(401, { detail: "Invalid or expired token" });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

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
  setTokens("old-access", "refresh-1");
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.resetModules();
  // Only defined by installFakeLocks; jsdom has no navigator.locks.
  delete (navigator as { locks?: unknown }).locks;
});

describe("rotation", () => {
  it("a refresh stores the rotated refresh token with the new access token", async () => {
    stubFetch((call) => {
      if (call.path === "/auth/refresh") {
        return json(200, {
          access_token: "new-access",
          refresh_token: "refresh-2",
          token_type: "bearer",
        });
      }
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED();
    });

    await expect(getTender("a")).resolves.toEqual({ id: "/tenders/a" });

    expect(getAccessToken()).toBe("new-access");
    expect(getRefreshToken()).toBe("refresh-2");
    expect(JSON.parse(callsTo("/auth/refresh")[0].body!)).toEqual({ refresh_token: "refresh-1" });
  });

  it("without navigator.locks the refresh still runs, unlocked", async () => {
    expect("locks" in navigator).toBe(false);
    stubFetch((call) =>
      call.path === "/auth/refresh"
        ? json(200, { access_token: "new-access", refresh_token: "refresh-2" })
        : call.headers["Authorization"] === "Bearer new-access"
          ? json(200, { id: call.path })
          : EXPIRED(),
    );

    await expect(getTender("a")).resolves.toEqual({ id: "/tenders/a" });
    expect(callsTo("/auth/refresh")).toHaveLength(1);
  });
});

describe("two tabs", () => {
  // Each tab is its own instance of client.ts (its own single-flight
  // promise), sharing localStorage and navigator.locks, as two browser tabs
  // of one origin do.
  async function twoTabs() {
    vi.resetModules();
    const tabA = await import("./client");
    vi.resetModules();
    const tabB = await import("./client");
    return { tabA, tabB };
  }

  it("whose access tokens expire together make one refresh call, under the lock", async () => {
    const { requests } = installFakeLocks();
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    stubFetch(async (call) => {
      if (call.path === "/auth/refresh") {
        await refreshHeld;
        return json(200, { access_token: "new-access", refresh_token: "refresh-2" });
      }
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED();
    });
    const { tabA, tabB } = await twoTabs();

    const both = Promise.all([tabA.getTender("a"), tabB.getTender("b")]);
    // Both tabs got their 401 and asked for the lock while the first
    // refresh is still held, so the second really waited for the first.
    await settle();
    expect(requests).toEqual(["tenderos-refresh", "tenderos-refresh"]);
    releaseRefresh();

    await expect(both).resolves.toEqual([{ id: "/tenders/a" }, { id: "/tenders/b" }]);
    expect(callsTo("/auth/refresh")).toHaveLength(1);
    expect(getRefreshToken()).toBe("refresh-2");
  });

  it("a tab that waited while the other tab's refresh was rejected lands on login, with no refresh of its own", async () => {
    installFakeLocks();
    let releaseRefresh!: () => void;
    const refreshHeld = new Promise<void>((resolve) => (releaseRefresh = resolve));
    // Tab A's refresh is rejected (its family was revoked); tab A clears
    // storage, as a logout does. Tab B, waiting on the lock, must not send
    // the token it read before waiting.
    stubFetch(async (call) => {
      if (call.path === "/auth/refresh") {
        await refreshHeld;
        return EXPIRED();
      }
      return EXPIRED();
    });
    const { tabA, tabB } = await twoTabs();
    const expiredB = vi.fn();
    tabB.setSessionExpiredHandler(expiredB);

    const both = Promise.allSettled([tabA.getTender("a"), tabB.getTender("b")]);
    await settle();
    releaseRefresh();
    await both;

    expect(callsTo("/auth/refresh")).toHaveLength(1);
    expect(getRefreshToken()).toBeNull();
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
        return json(200, { access_token: "new-access", refresh_token: "refresh-2" });
      }
      return call.headers["Authorization"] === "Bearer new-access"
        ? json(200, { id: call.path })
        : EXPIRED();
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
    expect(getRefreshToken()).toBe("refresh-2");
    expect(expired).not.toHaveBeenCalled();
  });
});

describe("logout", () => {
  it("clears both tokens, then sends the refresh token it read to /auth/logout", async () => {
    let refreshDuringCall: string | null = "unset";
    let accessDuringCall: string | null = "unset";
    stubFetch(() => {
      refreshDuringCall = getRefreshToken();
      accessDuringCall = getAccessToken();
      return new Response(null, { status: 204 });
    });

    await logout();

    expect(callsTo("/auth/logout")).toHaveLength(1);
    expect(JSON.parse(callsTo("/auth/logout")[0].body!)).toEqual({ refresh_token: "refresh-1" });
    // Cleared before the call, not after. The order changed in plan item 39's
    // review: cleared after it, a tab closed during the wait of up to 5 s left
    // this browser signed in.
    expect(refreshDuringCall).toBeNull();
    expect(accessDuringCall).toBeNull();
    expect(callsTo("/auth/logout")[0].headers["Authorization"]).toBeUndefined();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it("still clears the tokens when the call cannot reach the API", async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });

    await expect(logout()).resolves.toBeUndefined();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it("still clears the tokens when the API answers with an error", async () => {
    stubFetch(() => json(503, { detail: "unavailable" }));

    await logout();
    expect(getRefreshToken()).toBeNull();
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

  it("still clears the tokens when the call hangs, ending within its 5 s bound", async () => {
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
    expect(getRefreshToken()).toBeNull();
    expect(isLoggingOut()).toBe(false);
  }, 10_000);

  it("with no refresh token stored makes no call and still clears", async () => {
    localStorage.removeItem("tenderos_refresh_token");
    stubFetch(() => new Response(null, { status: 204 }));

    await logout();

    expect(calls).toHaveLength(0);
    expect(getAccessToken()).toBeNull();
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
  it("calls /auth/logout, clears the tokens and ends the session", async () => {
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
    expect(getRefreshToken()).toBeNull();
  });
});
