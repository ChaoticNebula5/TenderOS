import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { Button } from "./Button";
import { buttonClassName } from "./buttonClassName";

// The stylesheet's own text: vitest does not hand CSS to tests (a ?raw
// import comes back empty), so the rule is read from the file itself -
// resolved from apps/web, where vitest runs (under jsdom import.meta.url
// is not a file: URL).
const buttonCss = readFileSync(resolve("src/components/ui/Button.css"), "utf8");

// Same as the existing render tests: vitest globals are off, so Testing
// Library cannot register its own automatic cleanup.
afterEach(() => {
  cleanup();
});

describe("Button", () => {
  it.each(["primary", "secondary", "quiet", "danger"] as const)(
    "renders the %s variant class",
    (variant) => {
      render(<Button variant={variant}>Approve</Button>);
      expect(screen.getByRole("button", { name: "Approve" })).toHaveClass("btn", `btn--${variant}`);
    },
  );

  it("defaults to secondary and type=button", () => {
    render(<Button>Preview impact</Button>);
    const button = screen.getByRole("button", { name: "Preview impact" });
    expect(button).toHaveClass("btn--secondary");
    expect(button).toHaveAttribute("type", "button");
  });

  it("keeps an explicit type=submit", () => {
    render(<Button type="submit">Create bid</Button>);
    expect(screen.getByRole("button", { name: "Create bid" })).toHaveAttribute("type", "submit");
  });

  it("when busy: shows the busy label, sets aria-busy and is disabled", () => {
    render(
      <Button variant="primary" busy busyLabel="Creating...">
        Create bid
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Creating..." });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    expect(screen.queryByText("Create bid")).toBeNull();
  });

  it("when not busy: no aria-busy attribute and enabled", () => {
    render(<Button busyLabel="Creating...">Create bid</Button>);
    const button = screen.getByRole("button", { name: "Create bid" });
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button).toBeEnabled();
  });

  it("passes disabled through without being busy", () => {
    render(<Button disabled>Reject</Button>);
    const button = screen.getByRole("button", { name: "Reject" });
    expect(button).toBeDisabled();
    expect(button).not.toHaveAttribute("aria-busy");
  });

  // Polish pass: a quiet button is a control, not text - it draws the 3:1
  // control border on a transparent fill. jsdom applies no stylesheet, so
  // this pins the rule itself; the browser harness measures the result.
  it("gives the quiet variant the control border and no fill", () => {
    const rule = /\.btn--quiet\s*\{([^}]*)\}/.exec(buttonCss)?.[1] ?? "";
    expect(rule).toMatch(/border-color:\s*var\(--control-border\)/);
    expect(rule).toMatch(/background-color:\s*transparent/);
  });

  it("appends an extra class through buttonClassName", () => {
    expect(buttonClassName("primary", "extra")).toBe("btn btn--primary extra");
    expect(buttonClassName()).toBe("btn btn--secondary");
  });
});
