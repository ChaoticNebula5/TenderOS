import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext } from "../auth/authContextValue";
import { Login } from "./Login";

// Phase 4 plan item 2 (decision 0041). fetch is stubbed rather than the
// client module mocked, so the real path runs: client.ts reads Retry-After
// into ApiError.retryAfterSeconds, formatErrorDetail words it, and Login
// shows it.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function loginUnavailable(retryAfter: string | null): Response {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (retryAfter !== null) headers.set("Retry-After", retryAfter);
  return new Response(
    JSON.stringify({
      error: {
        code: "LOGIN_UNAVAILABLE",
        message: "Sign-in is briefly unavailable. Please try again shortly.",
      },
    }),
    { status: 503, headers },
  );
}

function renderLogin() {
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthContext.Provider
        value={{
          meResponse: null,
          organizationId: null,
          refresh: async () => {},
          logout: async () => {},
        }}
      >
        <Login />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

// Two buttons are named "Log in": the mode switch and the submit.
function submitButton(): HTMLElement {
  const submit = screen
    .getAllByRole("button", { name: "Log in" })
    .find((button) => button.getAttribute("type") === "submit");
  if (!submit) throw new Error("no submit button named Log in");
  return submit;
}

async function submitLogin() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Email"), "person@example.com");
  await user.type(screen.getByLabelText("Password"), "a password");
  await user.click(submitButton());
}

describe("login while the API refuses sign-in (503 LOGIN_UNAVAILABLE)", () => {
  it("shows the wait from Retry-After and does not retry on its own", async () => {
    const fetchMock = vi.fn(async () => loginUnavailable("30"));
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await submitLogin();

    expect(
      await screen.findByText("Sign-in is briefly unavailable. Try again in 30 seconds."),
    ).toBeInTheDocument();
    // The message appears only once the submit handler has finished, so a
    // client that retried would have called fetch more than once by now.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(submitButton()).toBeEnabled();
  });

  it("without a readable Retry-After it still says what happened", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => loginUnavailable(null)),
    );
    renderLogin();

    await submitLogin();

    expect(
      await screen.findByText("Sign-in is briefly unavailable. Try again in a moment."),
    ).toBeInTheDocument();
  });
});
