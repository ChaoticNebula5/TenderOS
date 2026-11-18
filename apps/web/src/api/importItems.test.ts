import { afterEach, describe, expect, it, vi } from "vitest";

import { approveImportItem } from "./client";

// Plan item 80 (PR 18): Approve on a resume item sends the fingerprint of the
// employee values the row showed, which the API compares before merging
// (409 IMPORT_ITEM_TARGET_CHANGED when they moved). The screen's tests mock
// this function, so the request body itself is pinned here.

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch() {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({ id: "i1", status: "approved" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentBody(fetchMock: ReturnType<typeof stubFetch>): unknown {
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toContain("/knowledge/import-items/i1/approve");
  expect(init.method).toBe("POST");
  return JSON.parse(String(init.body));
}

describe("approveImportItem", () => {
  it("sends the target fingerprint it is given", async () => {
    const fetchMock = stubFetch();
    await approveImportItem("i1", "fp-1");
    expect(sentBody(fetchMock)).toEqual({ target_fingerprint: "fp-1" });
  });

  it("sends a null fingerprint for an item that names no employee", async () => {
    const fetchMock = stubFetch();
    await approveImportItem("i1");
    expect(sentBody(fetchMock)).toEqual({ target_fingerprint: null });
  });
});
