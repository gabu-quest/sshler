import { beforeEach, describe, expect, it } from "vitest";

import { useDiffHistory } from "./useDiffHistory";

describe("useDiffHistory", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("starts empty", () => {
    const h = useDiffHistory();
    expect(h.list()).toEqual([]);
  });

  it("records and lists in newest-first order", () => {
    const h = useDiffHistory();
    h.record("aaa", "first");
    h.record("bbb", "second");
    const got = h.list();
    expect(got).toHaveLength(2);
    expect(got[0]!.b64).toBe("bbb");
    expect(got[0]!.label).toBe("second");
    expect(got[1]!.b64).toBe("aaa");
  });

  it("dedupes on b64 (re-recording moves it to the front, no duplicate)", () => {
    const h = useDiffHistory();
    h.record("aaa", "first");
    h.record("bbb", "second");
    h.record("aaa", "first-again");
    const got = h.list();
    expect(got).toHaveLength(2);
    expect(got[0]!.b64).toBe("aaa");
    expect(got[0]!.label).toBe("first-again");
    expect(got[1]!.b64).toBe("bbb");
  });

  it("caps the list at 10 entries", () => {
    const h = useDiffHistory();
    for (let i = 0; i < 15; i++) {
      h.record(`k${i}`, `label-${i}`);
    }
    const got = h.list();
    expect(got).toHaveLength(10);
    expect(got[0]!.b64).toBe("k14");
    expect(got[9]!.b64).toBe("k5");
  });

  it("ignores empty b64", () => {
    const h = useDiffHistory();
    h.record("", "nothing");
    expect(h.list()).toEqual([]);
  });

  it("remove drops a specific entry", () => {
    const h = useDiffHistory();
    h.record("aaa", "a");
    h.record("bbb", "b");
    h.remove("aaa");
    const got = h.list();
    expect(got).toHaveLength(1);
    expect(got[0]!.b64).toBe("bbb");
  });

  it("clear wipes everything", () => {
    const h = useDiffHistory();
    h.record("aaa", "a");
    h.record("bbb", "b");
    h.clear();
    expect(h.list()).toEqual([]);
  });

  it("silently ignores malformed storage and starts fresh", () => {
    localStorage.setItem("sshler:diff:history", "{not valid json");
    const h = useDiffHistory();
    expect(h.list()).toEqual([]);
    h.record("aaa", "a");
    expect(h.list()).toHaveLength(1);
  });

  it("silently ignores wrong-version storage", () => {
    localStorage.setItem(
      "sshler:diff:history",
      JSON.stringify({ v: 99, notebooks: [{ b64: "x", label: "x", savedAt: 1 }] }),
    );
    const h = useDiffHistory();
    expect(h.list()).toEqual([]);
  });
});
