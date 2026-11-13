import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOGOUT_MARKER_KEY, getAccessToken, me, startSession } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { RequireAuth } from "../auth/RequireAuth";
import { Login } from "../routes/Login";
import { AppShell } from "./AppShell";

// Plan item 39: the Log out button, end to end inside the browser. The real
// AuthProvider, client, RequireAuth, shell and Login, with only fetch stubbed,
// so these fail if any link between the click and the login screen breaks.
// AppShell.test.tsx keeps the shell's element rules.
//
// Plan item 34, option 2: the refresh token is an HttpOnly cookie, so the
// client cannot clear it; the api's answer to /auth/logout does. A marker in
// localStorage holds the log out until that answer comes, and tells the
// other tabs.

interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body?: string;
}

let calls: Call[];
// What /auth/logout answers. Each test sets its own.
let answerLogout: (init?: RequestInit) => Response | Promise<Response>;
// When true, /auth/me and /auth/refresh answer 401: the session has expired.
// /auth/refresh answers 401 in every test here: no test needs a refresh to
// work, and one that sent one by mistake would land on login and fail.
let sessionExpired: boolean;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const callsTo = (path: string) => calls.filter((call) => call.path === path);

beforeEach(() => {
  calls = [];
  sessionExpired = false;
  answerLogout = () => new Response(null, { status: 204 });
  startSession("access-1");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const path = new URL(input).pathname;
      calls.push({
        method: init?.method ?? "GET",
        path,
        headers: { ...(init?.headers as Record<string, string>) },
        body: init?.body as string | undefined,
      });
      if (path === "/auth/logout") return answerLogout(init);
      if (path === "/auth/login") {
        return json(200, { access_token: "access-2" });
      }
      if (sessionExpired || path === "/auth/refresh") {
        return json(401, { detail: "Invalid or expired token" });
      }
      if (path === "/auth/me") {
        // As the API does: no token, no answer.
        if (!(init?.headers as Record<string, string>)["Authorization"]) {
          return json(401, { detail: "Not authenticated" });
        }
        return json(200, {
          user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
          memberships: [{ organization_id: "org-1", role: "member" }],
        });
      }
      throw new Error(`unexpected call to ${path}`);
    }),
  );
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

function WhereAmI() {
  return <p data-testid="path">{useLocation().pathname}</p>;
}

// A signed-in screen that makes one authenticated request, the way any real
// screen would find out its session had expired.
function TenderScreen() {
  const [, setError] = useState<unknown>(null);
  return (
    <main>
      tender screen
      <button type="button" onClick={() => void me().catch(setError)}>
        make a request
      </button>
    </main>
  );
}

function renderSignedInAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <RequireAuth>
                <AppShell />
              </RequireAuth>
            }
          >
            <Route
              path="/"
              element={
                <main>
                  dashboard screen <Link to="/tenders/t1">open the tender</Link>
                </main>
              }
            />
            <Route path="/tenders/:tenderId" element={<TenderScreen />} />
          </Route>
        </Routes>
        <WhereAmI />
      </AuthProvider>
    </MemoryRouter>,
  );
}

// The button's name is "Log out <email>" (AppShell.test.tsx pins it).
const LOG_OUT = /^Log out/;
const logOutButton = () => screen.findByRole("button", { name: LOG_OUT });

async function expectOnLoginSignedOut() {
  expect(await screen.findByRole("heading", { name: "TenderOS" })).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent("/login");
  expect(screen.queryByRole("button", { name: LOG_OUT })).toBeNull();
  expect(screen.queryByText("person@example.com")).toBeNull();
  expect(getAccessToken()).toBeNull();
}

async function logInThroughTheForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Email"), "person@example.com");
  await user.type(screen.getByLabelText("Password"), "a-password");
  // Two buttons say "Log in": the mode switch, and this one, the form's submit.
  const form = screen.getByLabelText("Email").closest("form")!;
  await user.click(within(form).getByRole("button", { name: "Log in" }));
}

describe("the Log out button", () => {
  it("calls /auth/logout, lands on the login screen, and clears the marker on 204", async () => {
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");

    await user.click(await logOutButton());

    await expectOnLoginSignedOut();
    expect(callsTo("/auth/logout")).toHaveLength(1);
    const [call] = callsTo("/auth/logout");
    expect(call.method).toBe("POST");
    // The cookie is the browser's to send; the client sends no body.
    expect(call.body).toBeUndefined();
    expect(call.headers["X-TenderOS-Client"]).toBe("web");
    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).toBeNull();
  });

  it("is disabled and shows it is working until logout() finishes, then lands", async () => {
    let answer: (response: Response) => void = () => {};
    answerLogout = () => new Promise<Response>((resolve) => (answer = resolve));
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");

    await user.click(await logOutButton());

    const busy = await screen.findByRole("button", { name: "Logging out..." });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    // Still waiting, on the same screen. The access token is already gone and
    // the marker set, so a tab closed now stays signed out (plan item 39's
    // review; the marker since plan item 34, option 2).
    expect(screen.getByText("tender screen")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/tenders/t1");
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).not.toBeNull();

    // A second click while it runs sends nothing more.
    await user.click(busy);
    expect(callsTo("/auth/logout")).toHaveLength(1);

    answer(new Response(null, { status: 204 }));

    await expectOnLoginSignedOut();
  });

  it.each<[string, () => Response]>([
    [
      "the API cannot be reached",
      () => {
        throw new TypeError("Failed to fetch");
      },
    ],
    ["the API answers with an error", () => json(503, { detail: "unavailable" })],
  ])("still ends the session and lands on login when %s", async (_name, answer) => {
    answerLogout = answer;
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");

    await user.click(await logOutButton());

    await expectOnLoginSignedOut();
    expect(callsTo("/auth/logout")).toHaveLength(1);
    // Kept for the next page load to send the call again.
    expect(localStorage.getItem(LOGOUT_MARKER_KEY)).not.toBeNull();
  });

  it("still ends the session when the call hangs, after logout()'s 5 s bound", async () => {
    // Real time: the bound is AbortSignal.timeout, which fake timers don't drive.
    answerLogout = (init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal!.reason));
      });
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");

    await user.click(await logOutButton());
    expect(await screen.findByRole("button", { name: "Logging out..." })).toBeDisabled();

    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/login"), {
      timeout: 7_000,
    });
    await expectOnLoginSignedOut();
  }, 10_000);

  it("does not send the next login back to the page logged out from", async () => {
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");
    await user.click(await logOutButton());
    await expectOnLoginSignedOut();

    await logInThroughTheForm(user);

    expect(await screen.findByText("dashboard screen")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
    expect(screen.getByTestId("path")).not.toHaveTextContent("/tenders");
  });

  it("sends a screen polling during the wait to login with no page to return to", async () => {
    let answer: (response: Response) => void = () => {};
    answerLogout = () => new Promise<Response>((resolve) => (answer = resolve));
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");
    await user.click(await logOutButton());
    expect(await screen.findByRole("button", { name: "Logging out..." })).toBeDisabled();

    // The screen's next poll, with storage already empty.
    await user.click(screen.getByRole("button", { name: "make a request" }));

    // On login while the call is still out, and nothing tried to refresh.
    expect(await screen.findByRole("heading", { name: "TenderOS" })).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/login");
    expect(callsTo("/auth/refresh")).toHaveLength(0);

    answer(new Response(null, { status: 204 }));
    await expectOnLoginSignedOut();

    await logInThroughTheForm(user);

    expect(await screen.findByText("dashboard screen")).toBeInTheDocument();
    expect(screen.getByTestId("path")).not.toHaveTextContent("/tenders");
  });

  it("keeps a session signed in during the wait: the login waits for the old call", async () => {
    // Plan item 34, option 2: the old call's answer clears the cookie by name
    // and path, so it must come before the new login's Set-Cookie, not after.
    let answer: (response: Response) => void = () => {};
    answerLogout = () => new Promise<Response>((resolve) => (answer = resolve));
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");
    await user.click(await logOutButton());
    await user.click(await screen.findByRole("button", { name: "make a request" }));
    await screen.findByRole("heading", { name: "TenderOS" });

    await logInThroughTheForm(user);
    expect(callsTo("/auth/login")).toHaveLength(0);

    answer(new Response(null, { status: 204 }));

    expect(await screen.findByText("dashboard screen")).toBeInTheDocument();
    const order = calls.map((call) => call.path).filter((path) => path.startsWith("/auth/lo"));
    expect(order).toEqual(["/auth/logout", "/auth/login"]);
    // Give the old logout() every chance to end the new session.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByText("dashboard screen")).toBeInTheDocument();
    expect(screen.getByText("person@example.com")).toBeInTheDocument();
    expect(getAccessToken()).toBe("access-2");
  });

  it.each<[string, () => Response]>([
    ["worked", () => new Response(null, { status: 204 })],
    [
      "failed",
      () => {
        throw new TypeError("Failed to fetch");
      },
    ],
  ])(
    "leaves an expired session's return to its page working, after a log out that %s",
    async (_name, logoutAnswer) => {
      answerLogout = logoutAnswer;
      const user = userEvent.setup();
      renderSignedInAt("/");
      await user.click(await logOutButton());
      await expectOnLoginSignedOut();
      await logInThroughTheForm(user);

      // Same AuthProvider throughout: signed in again, on to a tender, and the
      // session expires there.
      await user.click(await screen.findByRole("link", { name: "open the tender" }));
      sessionExpired = true;
      await user.click(await screen.findByRole("button", { name: "make a request" }));
      expect(await screen.findByRole("heading", { name: "TenderOS" })).toBeInTheDocument();
      expect(screen.getByTestId("path")).toHaveTextContent("/login");
      sessionExpired = false;

      await logInThroughTheForm(user);

      expect(await screen.findByText("tender screen")).toBeInTheDocument();
      expect(screen.getByTestId("path")).toHaveTextContent("/tenders/t1");
    },
  );

  it("another tab's log out (the marker's storage event) lands this tab on login", async () => {
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");
    await logOutButton();

    window.dispatchEvent(
      new StorageEvent("storage", { key: LOGOUT_MARKER_KEY, newValue: "2026-11-13T10:00:00Z" }),
    );

    await expectOnLoginSignedOut();
    // This tab made no call: the tab that logged out did.
    expect(callsTo("/auth/logout")).toHaveLength(0);
    // A deliberate log out: the next login does not return to the tender.
    await logInThroughTheForm(user);
    expect(await screen.findByText("dashboard screen")).toBeInTheDocument();
    expect(screen.getByTestId("path")).not.toHaveTextContent("/tenders");
  });

  it.each<[string, StorageEventInit]>([
    ["the marker removed", { key: LOGOUT_MARKER_KEY, newValue: null }],
    ["another key", { key: "something_else", newValue: "x" }],
    ["localStorage cleared", { key: null }],
  ])("ignores a storage event for %s", async (_name, init) => {
    renderSignedInAt("/tenders/t1");
    await logOutButton();

    window.dispatchEvent(new StorageEvent("storage", init));

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText("tender screen")).toBeInTheDocument();
    expect(getAccessToken()).toBe("access-1");
  });

  it.each<[string, () => Response, boolean]>([
    ["answers 204, and the marker is cleared", () => new Response(null, { status: 204 }), false],
    ["fails, and the marker stays", () => json(503, { detail: "unavailable" }), true],
  ])(
    "a load with the marker present sends the log out again, shows login, and when it %s",
    async (_name, answer, markerAfter) => {
      localStorage.setItem(LOGOUT_MARKER_KEY, "2026-11-13T10:00:00Z");
      answerLogout = answer;

      renderSignedInAt("/tenders/t1");

      expect(await screen.findByRole("heading", { name: "TenderOS" })).toBeInTheDocument();
      await waitFor(() => expect(callsTo("/auth/logout")).toHaveLength(1));
      // No session was looked up or restored.
      expect(callsTo("/auth/me")).toHaveLength(0);
      expect(callsTo("/auth/refresh")).toHaveLength(0);
      expect(getAccessToken()).toBeNull();
      await waitFor(() =>
        expect(localStorage.getItem(LOGOUT_MARKER_KEY) !== null).toBe(markerAfter),
      );
    },
  );
});
