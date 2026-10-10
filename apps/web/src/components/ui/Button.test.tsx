import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Button } from "./Button";
import { buttonClassName } from "./buttonClassName";

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
      expect(screen.getByRole("button", { name: "Approve" })).toHaveClass(
        "btn",
        `btn--${variant}`,
      );
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

  it("appends an extra class through buttonClassName", () => {
    expect(buttonClassName("primary", "extra")).toBe("btn btn--primary extra");
    expect(buttonClassName()).toBe("btn btn--secondary");
  });
});
