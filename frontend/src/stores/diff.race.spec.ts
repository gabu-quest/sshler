import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import { useDiffStore } from "./diff";

vi.mock("@/api/http", () => ({ gitShow: vi.fn() }));
import { gitShow } from "@/api/http";

const mockedGitShow = vi.mocked(gitShow);

type Show = Awaited<ReturnType<typeof gitShow>>;
interface Deferred {
  path: string;
  ref: string;
  resolve: (content: string) => void;
  reject: (err: unknown) => void;
}

// Every gitShow call waits until the test settles it.
function holdRequests(): Deferred[] {
  const calls: Deferred[] = [];
  mockedGitShow.mockImplementation(
    (_box: string, _dir: string, path: string, ref: string) =>
      new Promise<Show>((resolve, reject) => {
        calls.push({
          path,
          ref,
          resolve: (content) => resolve({ content, ref, path } as Show),
          reject,
        });
      }),
  );
  return calls;
}

const side = (ref: string, path = "a.ts") => ({ box: "local", directory: "/r", ref, path });
const flush = () => new Promise((r) => setTimeout(r, 0));

function loadedStore() {
  const store = useDiffStore();
  store.setSide(0, "left", side("main"));
  store.setSide(0, "right", side("feat"));
  return store;
}

describe("diff store: loads and swaps", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    mockedGitShow.mockReset();
  });

  it("swapping a loaded cell redraws at once with sides reversed and asks for no reload", async () => {
    const calls = holdRequests();
    const store = loadedStore();
    const p = store.fetchCell(0, "t");
    calls[0]!.resolve("L");
    calls[1]!.resolve("R");
    await p;
    expect(store.cells[0]!.status).toBe("ready");

    const needsFetch = store.swapSides(0);

    expect(needsFetch).toBe(false);
    expect(store.cells[0]!.status).toBe("ready");
    expect(store.cells[0]!.left.content).toBe("R");
    expect(store.cells[0]!.left.config.ref).toBe("feat");
    expect(store.cells[0]!.right.content).toBe("L");
    expect(store.cells[0]!.right.status).toBe("loaded");
    expect(mockedGitShow).toHaveBeenCalledTimes(2);
  });

  it("a swap during a load ends with each content on its own side", async () => {
    const calls = holdRequests();
    const store = loadedStore();
    const p = store.fetchCell(0, "t");
    store.swapSides(0);
    calls[0]!.resolve("L"); // the main side
    calls[1]!.resolve("R"); // the feat side
    await p;
    await flush();

    const c = store.cells[0]!;
    expect(c.left.config.ref).toBe("feat");
    expect(c.left.content).toBe("R");
    expect(c.right.config.ref).toBe("main");
    expect(c.right.content).toBe("L");
    expect(c.status).toBe("ready");
    expect(mockedGitShow).toHaveBeenCalledTimes(2);
  });

  it("when two loads of one cell overlap, the newest wins and the older writes nothing", async () => {
    const calls = holdRequests();
    const store = loadedStore();
    const first = store.fetchCell(0, "t");
    const second = store.fetchCell(0, "t");
    expect(calls).toHaveLength(4);
    // the newer load answers first, the older one answers late
    calls[2]!.resolve("new-L");
    calls[3]!.resolve("new-R");
    await second;
    calls[0]!.resolve("old-L");
    calls[1]!.resolve("old-R");
    await first;

    const c = store.cells[0]!;
    expect(c.left.content).toBe("new-L");
    expect(c.right.content).toBe("new-R");
    expect(c.status).toBe("ready");
  });

  it("a cell moved during a load gets its own result", async () => {
    const calls = holdRequests();
    const store = useDiffStore();
    store.setSide(0, "left", side("main", "one.ts"));
    store.setSide(0, "right", side("feat", "one.ts"));
    store.addCell();
    store.setSide(1, "left", side("main", "two.ts"));
    store.setSide(1, "right", side("feat", "two.ts"));

    const p = store.fetchCell(0, "t");
    store.swapCells(0, 1);
    calls.find((c) => c.path === "one.ts" && c.ref === "main")!.resolve("one-L");
    calls.find((c) => c.path === "one.ts" && c.ref === "feat")!.resolve("one-R");
    await p;

    expect(store.cells[1]!.left.config.path).toBe("one.ts");
    expect(store.cells[1]!.left.content).toBe("one-L");
    expect(store.cells[1]!.status).toBe("ready");
    expect(store.cells[0]!.left.config.path).toBe("two.ts");
    expect(store.cells[0]!.left.content).toBe("");
    expect(store.cells[0]!.status).toBe("idle");
  });

  it("a cell removed during a load leaves the cell now at that index alone", async () => {
    const calls = holdRequests();
    const store = useDiffStore();
    store.setSide(0, "left", side("main", "one.ts"));
    store.setSide(0, "right", side("feat", "one.ts"));
    store.addCell();
    store.setSide(1, "left", side("main", "two.ts"));
    store.setSide(1, "right", side("feat", "two.ts"));

    const p = store.fetchCell(0, "t");
    store.removeCell(0);
    for (const c of calls) c.resolve(`got-${c.path}`);
    await p;

    expect(store.cellCount).toBe(1);
    expect(store.cells[0]!.left.config.path).toBe("two.ts");
    expect(store.cells[0]!.left.content).toBe("");
    expect(store.cells[0]!.status).toBe("idle");
  });

  it("a side changed during a load drops the old answer", async () => {
    const calls = holdRequests();
    const store = loadedStore();
    const p = store.fetchCell(0, "t");
    store.setSide(0, "left", side("other"));
    calls[0]!.resolve("stale");
    calls[1]!.resolve("R");
    await p;

    expect(store.cells[0]!.left.content).toBe("");
    expect(store.cells[0]!.left.status).toBe("idle");
  });

  it("a swap still retries a side that failed", async () => {
    const calls = holdRequests();
    const store = loadedStore();
    const p = store.fetchCell(0, "t");
    calls[0]!.reject(new ApiError(500, "internal", "boom"));
    calls[1]!.resolve("R");
    await p;
    expect(store.cells[0]!.right.status).not.toBe("error");

    expect(store.swapSides(0)).toBe(true);
    expect(store.cells[0]!.right.status).toBe("error");
  });
});
