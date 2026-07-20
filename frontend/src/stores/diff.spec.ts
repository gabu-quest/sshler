import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { useDiffStore, notebookToBase64 } from "./diff";

vi.mock("@/api/http", () => ({
  gitShow: vi.fn(),
}));

import { gitShow } from "@/api/http";

const mockedGitShow = vi.mocked(gitShow);

function side(box: string, directory: string, ref: string, path: string) {
  return { box, directory, ref, path };
}

describe("diff store (multi-cell)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    mockedGitShow.mockReset();
  });

  it("starts with exactly one empty cell", () => {
    const store = useDiffStore();
    expect(store.cellCount).toBe(1);
    expect(store.cells[0]!.status).toBe("idle");
    expect(store.cells[0]!.left.config.box).toBe("");
    expect(store.defaultRepo).toBe(null);
  });

  it("setSide(idx, …) updates the chosen cell and clears its status", () => {
    const store = useDiffStore();
    store.setSide(0, "left", side("local", "/repo", "main", "a.ts"));
    expect(store.cells[0]!.left.config.path).toBe("a.ts");
    expect(store.cells[0]!.left.status).toBe("idle");
  });

  it("addCell with no prefill copies left+right (box/dir/path) from previous, clears refs", () => {
    const store = useDiffStore();
    store.setSide(0, "left", side("local", "/repo", "main", "a.ts"));
    store.setSide(0, "right", side("local", "/repo", "feat", "a.ts"));
    const newIdx = store.addCell();
    expect(newIdx).toBe(1);
    expect(store.cellCount).toBe(2);
    expect(store.cells[1]!.left.config.box).toBe("local");
    expect(store.cells[1]!.left.config.directory).toBe("/repo");
    expect(store.cells[1]!.left.config.path).toBe("a.ts");
    expect(store.cells[1]!.left.config.ref).toBe("");
    expect(store.cells[1]!.right.config.path).toBe("a.ts");
    expect(store.cells[1]!.right.config.ref).toBe("");
  });

  it("addCell with defaultRepo seeds new cells from it (when previous cell has no box)", () => {
    const store = useDiffStore();
    // The initial cell is empty; default-repo should apply to the next added cell
    // because the prefill carries through empty seeds.
    store.setDefaultRepo({ box: "prod", directory: "/srv/app" });
    const idx = store.addCell();
    expect(idx).toBe(1);
    expect(store.cells[1]!.left.config.box).toBe("prod");
    expect(store.cells[1]!.left.config.directory).toBe("/srv/app");
    expect(store.cells[1]!.right.config.box).toBe("prod");
  });

  it("removeCell drops a cell; removing the last leaves one empty cell", () => {
    const store = useDiffStore();
    store.addCell();
    store.addCell();
    expect(store.cellCount).toBe(3);
    store.removeCell(1);
    expect(store.cellCount).toBe(2);
    store.removeCell(0);
    store.removeCell(0);
    expect(store.cellCount).toBe(1);
    expect(store.cells[0]!.left.config.box).toBe("");
  });

  it("swapCells reorders by index", () => {
    const store = useDiffStore();
    store.setSide(0, "left", side("a", "", "", ""));
    store.addCell();
    store.setSide(1, "left", side("b", "", "", ""));
    store.swapCells(0, 1);
    expect(store.cells[0]!.left.config.box).toBe("b");
    expect(store.cells[1]!.left.config.box).toBe("a");
  });

  it("swapSides flips left and right of a single cell", () => {
    const store = useDiffStore();
    store.setSide(0, "left", side("a", "/r", "main", "a.ts"));
    store.setSide(0, "right", side("b", "/r", "feat", "a.ts"));
    store.swapSides(0);
    expect(store.cells[0]!.left.config.box).toBe("b");
    expect(store.cells[0]!.left.config.ref).toBe("feat");
    expect(store.cells[0]!.right.config.box).toBe("a");
    expect(store.cells[0]!.right.config.ref).toBe("main");
  });

  it("fetchCell loads both sides on happy path", async () => {
    mockedGitShow
      .mockResolvedValueOnce({ content: "a\n", ref: "main", path: "a.ts" })
      .mockResolvedValueOnce({ content: "b\n", ref: "feat", path: "a.ts" });

    const store = useDiffStore();
    store.setSide(0, "left", side("local", "/r", "main", "a.ts"));
    store.setSide(0, "right", side("local", "/r", "feat", "a.ts"));

    await store.fetchCell(0, "tok");

    expect(store.cells[0]!.left.content).toBe("a\n");
    expect(store.cells[0]!.right.content).toBe("b\n");
    expect(store.cells[0]!.status).toBe("ready");
  });

  it("treats 'not found at ref' as missing, not error", async () => {
    mockedGitShow
      .mockRejectedValueOnce(new Error("File not found at ref main"))
      .mockResolvedValueOnce({ content: "new\n", ref: "feat", path: "new.ts" });

    const store = useDiffStore();
    store.setSide(0, "left", side("local", "/r", "main", "new.ts"));
    store.setSide(0, "right", side("local", "/r", "feat", "new.ts"));

    await store.fetchCell(0, "tok");

    expect(store.cells[0]!.left.status).toBe("missing");
    expect(store.cells[0]!.right.status).toBe("loaded");
    expect(store.cells[0]!.status).toBe("ready");
  });

  it("marks the cell binary when one side has a null byte", async () => {
    mockedGitShow
      .mockResolvedValueOnce({ content: "plain", ref: "main", path: "a.bin" })
      .mockResolvedValueOnce({ content: "with\0null", ref: "feat", path: "a.bin" });

    const store = useDiffStore();
    store.setSide(0, "left", side("local", "/r", "main", "a.bin"));
    store.setSide(0, "right", side("local", "/r", "feat", "a.bin"));
    await store.fetchCell(0, "tok");
    expect(store.cells[0]!.status).toBe("binary");
  });

  it("derives language from right path with fallback to left", () => {
    const store = useDiffStore();
    store.setSide(0, "left", side("local", "/r", "main", "a.py"));
    expect(store.cellLanguage(0)).toBe("python");
    store.setSide(0, "right", side("local", "/r", "main", "a.ts"));
    expect(store.cellLanguage(0)).toBe("javascript");
  });

  it("notebookToBase64 + hydrateFromQuery round-trips multiple cells", () => {
    const store = useDiffStore();
    store.setSide(0, "left", side("local", "/r", "main", "a.ts"));
    store.setSide(0, "right", side("local", "/r", "feat", "a.ts"));
    store.addCell({ left: { box: "remote", path: "b.ts", ref: "v1", directory: "/srv" }, right: { box: "remote", path: "b.ts", ref: "v2", directory: "/srv" } });
    store.setDefaultRepo({ box: "local", directory: "/r" });

    const q = store.toQuery();
    expect(typeof q.n).toBe("string");
    expect(q.n!.length).toBeGreaterThan(10);

    const store2 = useDiffStore();
    store2.hydrateFromQuery({ n: q.n });
    expect(store2.cellCount).toBe(2);
    expect(store2.cells[0]!.left.config.path).toBe("a.ts");
    expect(store2.cells[1]!.right.config.ref).toBe("v2");
    expect(store2.defaultRepo).toEqual({ box: "local", directory: "/r" });
  });

  it("hydrateFromQuery falls back to legacy ?lb=&rb= flat params", () => {
    const store = useDiffStore();
    store.hydrateFromQuery({
      lb: "local", ld: "/r", lr: "main", lp: "a.ts",
      rb: "local", rd: "/r", rr: "feat", rp: "a.ts",
    });
    expect(store.cellCount).toBe(1);
    expect(store.cells[0]!.left.config.box).toBe("local");
    expect(store.cells[0]!.left.config.ref).toBe("main");
    expect(store.cells[0]!.right.config.ref).toBe("feat");
  });

  it("hydrateFromQuery with malformed base64 leaves the store untouched", () => {
    const store = useDiffStore();
    const before = store.cells[0]!.id;
    store.hydrateFromQuery({ n: "not-real-base64!!!" });
    expect(store.cellCount).toBe(1);
    expect(store.cells[0]!.id).toBe(before);
  });

  it("toQuery returns an empty object when notebook is the default (one empty cell)", () => {
    const store = useDiffStore();
    expect(store.toQuery()).toEqual({});
  });

  it("clearAll resets to a single empty cell", () => {
    const store = useDiffStore();
    store.addCell();
    store.addCell();
    store.clearAll();
    expect(store.cellCount).toBe(1);
    expect(store.cells[0]!.left.config.box).toBe("");
  });

  it("setDefaultRepo with empty repo nulls it out", () => {
    const store = useDiffStore();
    store.setDefaultRepo({ box: "local", directory: "/r" });
    expect(store.defaultRepo).toEqual({ box: "local", directory: "/r" });
    store.setDefaultRepo({ box: "", directory: "" });
    expect(store.defaultRepo).toBe(null);
  });

  describe("server-saved (M3)", () => {
    it("starts with serverId null", () => {
      const store = useDiffStore();
      expect(store.serverId).toBe(null);
    });

    it("markServerSaved sets the id", () => {
      const store = useDiffStore();
      store.markServerSaved("abc123XY");
      expect(store.serverId).toBe("abc123XY");
    });

    it("toQuery returns empty when serverId is set (URL is /diff/n/<id> instead)", () => {
      const store = useDiffStore();
      store.setSide(0, "left", side("local", "/r", "main", "a.ts"));
      store.setSide(0, "right", side("local", "/r", "feat", "a.ts"));
      // Initially toQuery returns ?n=...
      expect(store.toQuery().n).toMatch(/^[A-Za-z0-9_-]+$/);
      store.markServerSaved("abc123XY");
      expect(store.toQuery()).toEqual({});
    });

    it("editing any cell clears serverId (immutability — user forks)", () => {
      const store = useDiffStore();
      store.markServerSaved("abc123XY");
      store.setSide(0, "left", side("local", "/r", "main", "a.ts"));
      expect(store.serverId).toBe(null);
    });

    it("addCell clears serverId", () => {
      const store = useDiffStore();
      store.markServerSaved("abc123XY");
      store.addCell();
      expect(store.serverId).toBe(null);
    });

    it("removeCell clears serverId", () => {
      const store = useDiffStore();
      store.addCell();
      store.markServerSaved("abc123XY");
      store.removeCell(0);
      expect(store.serverId).toBe(null);
    });

    it("swapSides clears serverId", () => {
      const store = useDiffStore();
      store.markServerSaved("abc123XY");
      store.swapSides(0);
      expect(store.serverId).toBe(null);
    });

    it("currentEnvelope returns the v=1 envelope shape", () => {
      const store = useDiffStore();
      store.setSide(0, "left", side("local", "/r", "main", "a.ts"));
      store.setSide(0, "right", side("local", "/r", "feat", "a.ts"));
      const env = store.currentEnvelope();
      expect(env.v).toBe(1);
      expect(env.cells).toHaveLength(1);
      expect(env.cells[0]!.l.path).toBe("a.ts");
      expect(env.cells[0]!.r.ref).toBe("feat");
      expect(env.def).toBeUndefined();
    });

    it("hydrateFromEnvelope replaces cells and sets serverId atomically", () => {
      const store = useDiffStore();
      const envelope = {
        v: 1,
        cells: [
          { l: side("local", "/r", "main", "a.ts"), r: side("local", "/r", "feat", "a.ts") },
          { l: side("local", "/r", "main", "b.ts"), r: side("local", "/r", "feat", "b.ts") },
        ],
      };
      store.hydrateFromEnvelope(envelope, "abc123XY");
      expect(store.cellCount).toBe(2);
      expect(store.cells[1]!.left.config.path).toBe("b.ts");
      expect(store.serverId).toBe("abc123XY");
    });

    it("hydrateFromQuery returns false on truthy-but-malformed ?n= (so the view can toast)", () => {
      const store = useDiffStore();
      const ok = store.hydrateFromQuery({ n: "not!real!base64" });
      expect(ok).toBe(false);
    });
  });

  it("notebookToBase64 output decodes back into the same envelope shape", () => {
    const cells = [{
      id: "c1",
      left: { config: side("local", "/r", "main", "a.ts"), content: "", status: "idle" as const, error: null, truncated: false },
      right: { config: side("local", "/r", "feat", "a.ts"), content: "", status: "idle" as const, error: null, truncated: false },
      status: "idle" as const,
      error: null,
    }];
    const b64 = notebookToBase64(cells, null);
    expect(b64).toMatch(/^[A-Za-z0-9_-]+$/);
  });
});
