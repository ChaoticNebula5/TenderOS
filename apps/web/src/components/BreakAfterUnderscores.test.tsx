import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BreakAfterUnderscores } from "./BreakAfterUnderscores";

// Polish pass: the shared helper. Its promise is that the text is unchanged
// (full-flow.spec.ts:1380 and :1409 compare the red-team category exactly)
// while a <wbr> follows every underscore. Fake values.

afterEach(() => {
  cleanup();
});

function renderIn(text: string) {
  const { container } = render(
    <span data-testid="cell">
      <BreakAfterUnderscores text={text} />
    </span>,
  );
  return container.querySelector("span")!;
}

describe("BreakAfterUnderscores", () => {
  it("keeps the text exactly and adds a <wbr> after each underscore", () => {
    const cell = renderIn("deselected_fact_in_use");
    expect(cell.textContent).toBe("deselected_fact_in_use");
    expect(cell.querySelectorAll("wbr")).toHaveLength(3);
  });

  it("adds nothing to text without underscores", () => {
    const cell = renderIn("Whole file");
    expect(cell.textContent).toBe("Whole file");
    expect(cell.querySelectorAll("wbr")).toHaveLength(0);
  });

  it("handles leading, trailing and doubled underscores without changing the text", () => {
    for (const text of ["_batch_parse_failure", "trailing_", "a__b"]) {
      expect(renderIn(text).textContent).toBe(text);
      cleanup();
    }
  });
});
