import { afterEach, describe, expect, it, vi } from "vitest";

import { approveCompliance } from "./client";

// Plan item 59 (PR 19, Q4): Approve on a compliance entry sends the status
// the row showed, which the API compares under the entry's row lock (409
// COMPLIANCE_ENTRY_CHANGED when it moved). The screen's tests mock this
// function, so the request body itself is pinned here.

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("approveCompliance", () => {
  it("sends the status the screen showed", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ id: "c1", human_approved: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await approveCompliance("c1", "partially_compliant");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/compliance/c1/approve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ status: "partially_compliant" });
  });
});
