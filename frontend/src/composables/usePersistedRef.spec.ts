import { nextTick } from "vue";
import { beforeEach, describe, expect, it } from "vitest";

import { isString, isStringArray, usePersistedRef } from "./usePersistedRef";

describe("usePersistedRef", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("falls back to the default when nothing is persisted", () => {
    const state = usePersistedRef<string>("test:missing", "fallback", isString);
    expect(state.value).toBe("fallback");
  });

  it("hydrates from pre-seeded localStorage (string)", () => {
    localStorage.setItem("test:filter", JSON.stringify("my query"));
    const state = usePersistedRef<string>("test:filter", "", isString);
    expect(state.value).toBe("my query");
  });

  it("hydrates from pre-seeded localStorage (string array)", () => {
    localStorage.setItem("test:expanded", JSON.stringify(["repo-a", "repo-b"]));
    const state = usePersistedRef<string[]>("test:expanded", [], isStringArray);
    expect(state.value).toEqual(["repo-a", "repo-b"]);
  });

  it("falls back to the default on malformed JSON", () => {
    localStorage.setItem("test:broken", "{not valid json");
    const state = usePersistedRef<string[]>("test:broken", [], isStringArray);
    expect(state.value).toEqual([]);
  });

  it("falls back to the default when the persisted shape fails validation", () => {
    // Valid JSON, wrong shape: a filter key holding an array instead of a string.
    localStorage.setItem("test:wrong-shape", JSON.stringify([1, 2, 3]));
    const state = usePersistedRef<string>("test:wrong-shape", "default", isString);
    expect(state.value).toBe("default");
  });

  it("falls back to the default when a string array contains non-string items", () => {
    localStorage.setItem("test:mixed", JSON.stringify(["ok", 42]));
    const state = usePersistedRef<string[]>("test:mixed", ["seed"], isStringArray);
    expect(state.value).toEqual(["seed"]);
  });

  it("writes updates back to localStorage", async () => {
    const state = usePersistedRef<string>("test:write", "", isString);
    state.value = "updated";
    await nextTick();
    expect(localStorage.getItem("test:write")).toBe(JSON.stringify("updated"));
  });

  it("writes array mutations back to localStorage (deep watch)", async () => {
    const state = usePersistedRef<string[]>("test:write-array", [], isStringArray);
    state.value = ["repo-a"];
    await nextTick();
    expect(localStorage.getItem("test:write-array")).toBe(JSON.stringify(["repo-a"]));

    state.value.push("repo-b");
    await nextTick();
    expect(localStorage.getItem("test:write-array")).toBe(
      JSON.stringify(["repo-a", "repo-b"]),
    );
  });

  it("a second instance reading the same key picks up the first instance's write", async () => {
    const first = usePersistedRef<string>("test:shared", "", isString);
    first.value = "persisted-value";
    await nextTick();

    const second = usePersistedRef<string>("test:shared", "should-not-see-this", isString);
    expect(second.value).toBe("persisted-value");
  });
});
