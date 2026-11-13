import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { MeResponse } from "../api/client";
import { AuthProvider } from "./AuthContext";
import { useAuth } from "./useAuth";

// B17 - closes this fix's own coverage gap, found during a CodeRabbit-
// triggered sweep of the "no recovery path from a transient failure"
// standing pattern (KNOWN_LIMITATIONS.md). GateDecisionPanel.test.tsx
// exercises retryTransient wired into handleDecide() against the real
// component; nothing anywhere exercised it wired into AuthContext's own
// refresh() against the real AuthProvider - destinationPreservation.
// test.tsx deliberately substitutes a hand-rolled context instead
// (its own comment says so directly), specifically to avoid depending on
// retryTransient's timing. This renders the real AuthProvider, mocks only
// `me()`, and drives the real backoff with fake timers - the one thing
// the substituted-context test was built to avoid, because this is the
// test that actually needs to face it.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return { ...actual, me: vi.fn() };
});

const me = vi.mocked(apiClient.me);

const MEMBER: MeResponse = {
  user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
  memberships: [{ organization_id: "org-1", role: "member" }],
};

// Renders meResponse's own three states directly - refresh() is fired by
// AuthProvider's own mount effect, not a click here, so there's no button
// to drive; observing the rendered state is the only hook this test needs.
function Probe() {
  const { meResponse } = useAuth();
  if (meResponse === undefined) return <p>loading</p>;
  if (meResponse === null) return <p>logged out</p>;
  return <p>logged in as {meResponse.user.email}</p>;
}

beforeEach(() => {
  // refresh() early-returns "logged out" without ever calling me() when
  // getAccessToken() is falsy - a real token has to be seeded first for
  // either scenario below to actually reach the retryTransient call this
  // test exists to exercise.
  apiClient.startSession("fake-access-token");
});

afterEach(() => {
  cleanup();
  // resetAllMocks, not clearAllMocks: the first test below queues a
  // mockRejectedValueOnce/mockResolvedValueOnce pair - clearAllMocks only
  // wipes call history, not queued "once" implementations, so an
  // unconsumed one would leak into the next test's me() call. Found by
  // actually breaking the wiring and watching the second test fail for
  // the wrong reason (a leaked resolved value), not by inspection.
  vi.resetAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  apiClient.clearTokens();
  localStorage.clear();
});

describe("AuthProvider.refresh() through the real retryTransient", () => {
  it("a transient failure recovers once retryTransient's own retry succeeds", async () => {
    vi.useFakeTimers();
    me.mockRejectedValueOnce(new apiClient.ApiError(500, {}));
    me.mockResolvedValueOnce(MEMBER);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(screen.getByText("logged in as person@example.com")).toBeInTheDocument();
    expect(me).toHaveBeenCalledTimes(2);
    // The transient path keeps the token - a 500 says nothing about
    // whether the session itself is valid.
    expect(apiClient.getAccessToken()).toBe("fake-access-token");
  });

  it("a 401 terminates without retrying and clears the token", async () => {
    // No fake timers needed: retryTransient's own isRetryable rejects 401
    // outright, so this re-throws on the very first attempt with no
    // backoff delay for a test to wait out - the same reasoning
    // GateDecisionPanel.test.tsx's identical-shaped 401 test already uses.
    me.mockRejectedValue(new apiClient.ApiError(401, {}));

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    expect(await screen.findByText("logged out")).toBeInTheDocument();
    expect(me).toHaveBeenCalledTimes(1);
    expect(apiClient.getAccessToken()).toBeNull();
  });
});

// Plan item 34, option 2: with no access token in this tab, start-up gets
// one from the refresh cookie before me(). fetch answers the refresh; me()
// stays mocked.
describe("AuthProvider start-up with no access token in this tab", () => {
  function stubRefresh(...answers: (() => Response)[]) {
    const fetchMock = vi.fn(async () => (answers.shift() ?? answers[answers.length - 1])());
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("a transient refresh failure is retried, then me() runs with the new token", async () => {
    apiClient.clearTokens();
    vi.useFakeTimers();
    const fetchMock = stubRefresh(
      () => new Response("{}", { status: 503 }),
      () => new Response(JSON.stringify({ access_token: "restored" }), { status: 200 }),
    );
    me.mockResolvedValueOnce(MEMBER);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(screen.getByText("logged in as person@example.com")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(me).toHaveBeenCalledTimes(1);
    expect(apiClient.getAccessToken()).toBe("restored");
  });

  it("deletes the access and refresh tokens an older client left in localStorage", async () => {
    localStorage.setItem("tenderos_access_token", "legacy-access");
    localStorage.setItem("tenderos_refresh_token", "legacy-refresh");
    me.mockResolvedValueOnce(MEMBER);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    expect(await screen.findByText("logged in as person@example.com")).toBeInTheDocument();
    expect(localStorage.getItem("tenderos_access_token")).toBeNull();
    expect(localStorage.getItem("tenderos_refresh_token")).toBeNull();
  });
});
