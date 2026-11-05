import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, getRefreshToken, me, setTokens } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { RequireAuth } from "../auth/RequireAuth";
import { Login } from "../routes/Login";
import { AppShell } from "./AppShell";

// Plan item 39: the Log out button, end to end inside the browser. The real
// AuthProvider, client, RequireAuth, shell and Login, with only fetch stubbed,
// so these fail if any link between the click and the login screen breaks.
// AppShell.test.tsx keeps the shell's element rules.

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
  setTokens("access-1", "refresh-1");
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
        return json(200, { access_token: "access-2", refresh_token: "refresh-2" });
      }
      if (sessionExpired) return json(401, { detail: "Invalid or expired token" });
      if (path === "/auth/me") {
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

const logOutButton = () => screen.findByRole("button", { name: "Log out" });

async function expectOnLoginSignedOut() {
  expect(await screen.findByRole("heading", { name: "TenderOS" })).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent("/login");
  expect(screen.queryByRole("button", { name: "Log out" })).toBeNull();
  expect(screen.queryByText("person@example.com")).toBeNull();
  expect(getAccessToken()).toBeNull();
  expect(getRefreshToken()).toBeNull();
}

async function logInThroughTheForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Email"), "person@example.com");
  await user.type(screen.getByLabelText("Password"), "a-password");
  // Two buttons say "Log in": the mode switch, and this one, the form's submit.
  const form = screen.getByLabelText("Email").closest("form")!;
  await user.click(within(form).getByRole("button", { name: "Log in" }));
}

describe("the Log out button", () => {
  it("sends the refresh token to /auth/logout and lands on the login screen", async () => {
    const user = userEvent.setup();
    renderSignedInAt("/tenders/t1");

    await user.click(await logOutButton());

    await expectOnLoginSignedOut();
    expect(callsTo("/auth/logout")).toHaveLength(1);
    const [call] = callsTo("/auth/logout");
    expect(call.method).toBe("POST");
    expect(JSON.parse(call.body!)).toEqual({ refresh_token: "refresh-1" });
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
    // Still waiting: on the same screen, still signed in, tokens untouched.
    expect(screen.getByText("tender screen")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/tenders/t1");
    expect(getRefreshToken()).toBe("refresh-1");

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

  it("leaves an expired session's return to its page working, even after a log out", async () => {
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
  });
});
