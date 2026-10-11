import { describe, expect, it } from "vitest";
import { humanize } from "./humanize";

describe("humanize", () => {
  it.each([
    ["requirement_extraction", "Requirement extraction"],
    ["compliance", "Compliance"],
    ["RED_TEAM_review", "Red team review"],
    ["double__underscore", "Double underscore"],
    ["_leading_and_trailing_", "Leading and trailing"],
    ["", ""],
  ])("%s -> %s", (input, expected) => {
    expect(humanize(input)).toBe(expected);
  });
});
