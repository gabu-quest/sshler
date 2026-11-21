import { render, fireEvent } from "@testing-library/vue";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import BoxSearchInput from "./BoxSearchInput.vue";
import type { ApiSession } from "@/api/types";

vi.mock("@/api/http", () => ({
  searchDirectories: vi.fn(),
}));

import { searchDirectories } from "@/api/http";
import { ApiError } from "@/api/apiError";

vi.mock("naive-ui", () => {
  const stub = (template: string) => ({ template });
  return {
    NInput: {
      template: `<div>
        <input :value="value" @input="$emit('update:value', $event.target.value)"
          @focus="$emit('focus')" @blur="$emit('blur')" :placeholder="placeholder" data-testid="search-input" />
        <slot name="suffix" />
      </div>`,
      props: ["value", "placeholder", "size", "clearable"],
      emits: ["update:value", "focus", "blur"],
    },
    NIcon: stub("<span><slot /></span>"),
    NSpin: stub("<span data-testid='spinner'>Loading...</span>"),
    NEmpty: { props: ["description"], template: "<div data-testid='empty'>{{ description }}</div>" },
  };
});

const search = searchDirectories as ReturnType<typeof vi.fn>;

function session(id: string, name: string, dir: string, active = true): ApiSession {
  return {
    id, box: "b", session_name: name, working_directory: dir,
    created_at: 1, last_accessed_at: 1, active, window_count: 1, metadata: {},
  };
}

function answer(...paths: string[]) {
  return { box: "b", query: "", results: paths.map((path) => ({ path, score: 1, source: "discovery" })) };
}

/** Every row of the open list as "kind: text", top to bottom. */
function rowsOf(container: Element): string[] {
  return Array.from(container.querySelectorAll(".search-result")).map((el) => {
    const words = Array.from(el.querySelectorAll(".result-name, .result-path")).map((s) => s.textContent);
    return `${el.getAttribute("data-kind")}: ${words.join(" ")}`;
  });
}

async function typeAndSearch(container: Element, text: string) {
  const input = container.querySelector("input") as HTMLInputElement;
  await fireEvent.update(input, text);
  await vi.advanceTimersByTimeAsync(300);
  return input;
}

function mount(sessions: ApiSession[]) {
  return render(BoxSearchInput, { props: { box: "b", token: "tok", sessions } });
}

describe("BoxSearchInput", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    search.mockReset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("lists a running session above the folders, each folder as a row", async () => {
    search.mockResolvedValue(answer("/h/sshl-x", "/h/old/sshlogs"));
    const { container } = mount([session("1", "sshler", "/h/projects/sshler")]);
    await typeAndSearch(container, "ssh");
    expect(rowsOf(container)).toEqual([
      "session: sshler /h/projects/sshler",
      "folder: /h/sshl-x",
      "folder: /h/old/sshlogs",
    ]);
  });

  it("shows the sessions at once and asks the folder search once, after the delay", async () => {
    search.mockResolvedValue(answer("/h/foo"));
    const { container } = mount([session("1", "foo", "/h/a/foo")]);
    const input = container.querySelector("input") as HTMLInputElement;
    await fireEvent.update(input, "f");
    await fireEvent.update(input, "fo");
    await fireEvent.update(input, "foo");
    expect(rowsOf(container)).toEqual(["session: foo /h/a/foo"]);
    expect(search).toHaveBeenCalledTimes(0);
    await vi.advanceTimersByTimeAsync(300);
    expect(search).toHaveBeenCalledTimes(1);
    expect(search).toHaveBeenCalledWith("b", "foo", "tok", 20, { root: undefined, files: undefined });
  });

  it("matches the folder of a session when the name does not match, and ignores case", async () => {
    search.mockResolvedValue(answer());
    const { container } = mount([session("1", "alpha", "/h/Work/Beta-Lib")]);
    await typeAndSearch(container, "beta-lib");
    expect(rowsOf(container)).toEqual(["session: alpha /h/Work/Beta-Lib"]);
  });

  it("does not list a session that is not running", async () => {
    search.mockResolvedValue(answer());
    const { container, getByTestId } = mount([session("1", "foo", "/h/foo", false)]);
    await typeAndSearch(container, "foo");
    expect(rowsOf(container)).toEqual([]);
    expect(getByTestId("empty").textContent).toBe("No matches found");
  });

  it("shows a folder once when its session is already listed with that folder", async () => {
    search.mockResolvedValue(answer("/h/projects/sshler", "/h/other/sshler-x"));
    const { container } = mount([session("1", "sshler", "/h/projects/sshler")]);
    await typeAndSearch(container, "sshler");
    expect(rowsOf(container)).toEqual([
      "session: sshler /h/projects/sshler",
      "folder: /h/other/sshler-x",
    ]);
  });

  it("shows both rows for a folder named like a running session but in another place", async () => {
    search.mockResolvedValue(answer("/h/elsewhere/sshler"));
    const { container } = mount([session("1", "sshler", "/h/projects/sshler")]);
    await typeAndSearch(container, "sshler");
    expect(rowsOf(container)).toEqual([
      "session: sshler /h/projects/sshler",
      "folder: /h/elsewhere/sshler",
    ]);
  });

  it("lists two running sessions that share a folder as two rows, and one row for a repeated session", async () => {
    search.mockResolvedValue(answer());
    const { container } = mount([
      session("1", "one", "/h/shared"),
      session("2", "two", "/h/shared"),
      session("3", "two", "/h/shared"),
    ]);
    await typeAndSearch(container, "shared");
    expect(rowsOf(container)).toEqual(["session: one /h/shared", "session: two /h/shared"]);
  });

  it("keeps the sessions and says why when the folder search fails", async () => {
    search.mockRejectedValue(new ApiError(502, "unreachable", "build-box did not answer"));
    const { container, getAllByTestId } = mount([session("1", "foo", "/h/foo")]);
    await typeAndSearch(container, "foo");
    expect(rowsOf(container)).toEqual(["session: foo /h/foo"]);
    expect(getAllByTestId("empty").map((el) => el.textContent)).toEqual(["Cannot connect to the box — build-box did not answer"]);
  });

  it("says so in one line when nothing matches, on a box with no sessions", async () => {
    search.mockResolvedValue(answer());
    const { container, getAllByTestId } = mount([]);
    await typeAndSearch(container, "zz");
    expect(getAllByTestId("empty").map((el) => el.textContent)).toEqual(["No matches found"]);
  });

  it("drops the answer to an old text", async () => {
    let first: (value: unknown) => void = () => {};
    search.mockImplementationOnce(() => new Promise((resolve) => { first = resolve; }));
    search.mockResolvedValueOnce(answer("/h/newer"));
    const { container } = mount([]);
    const input = await typeAndSearch(container, "old");
    await fireEvent.update(input, "newer");
    await vi.advanceTimersByTimeAsync(300);
    first(answer("/h/stale"));
    await vi.advanceTimersByTimeAsync(0);
    expect(rowsOf(container)).toEqual(["folder: /h/newer"]);
  });

  it("reads a slash, a space and a Japanese character as typed", async () => {
    search.mockResolvedValue(answer());
    const { container } = mount([session("1", "nihon", "/h/日本 語/proj")]);
    await typeAndSearch(container, "本 語/p");
    expect(search).toHaveBeenCalledWith("b", "本 語/p", "tok", 20, { root: undefined, files: undefined });
    expect(rowsOf(container)).toEqual(["session: nihon /h/日本 語/proj"]);
  });

  it("a mouse press on a session row emits pick-session with its name", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    await typeAndSearch(container, "foo");
    await fireEvent.mouseDown(container.querySelector(".search-result[data-kind='session']")!);
    expect(emitted()["pick-session"]).toEqual([["foo"]]);
  });

  it("a mouse press on a folder row emits pick-folder with its path", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    await typeAndSearch(container, "foo");
    await fireEvent.mouseDown(container.querySelector(".search-result[data-kind='folder']")!);
    expect(emitted()["pick-folder"]).toEqual([["/h/x/foo-two"]]);
  });

  it("Enter on the highlighted folder row emits pick-folder with its path", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "ArrowDown" });
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(emitted()["pick-folder"]).toEqual([["/h/x/foo-two"]]);
  });

  it("Escape closes the list, emits nothing and keeps the text", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "Escape" });
    expect(Object.keys(emitted()).filter((name) => name.startsWith("pick"))).toEqual([]);
    expect(input.value).toBe("foo");
  });

  it("ArrowDown on a dismissed list opens it and picks nothing", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "Escape" });
    await fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(rowsOf(container)).toEqual(["session: foo /h/a/foo", "folder: /h/x/foo-two"]);
    expect(Object.keys(emitted()).filter((name) => name.startsWith("pick"))).toEqual([]);
  });

  it("Enter during an input method composition picks nothing", async () => {
    search.mockResolvedValue(answer());
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(emitted()["pick-session"]).toBeUndefined();
  });

  it("Enter with key code 229 picks nothing", async () => {
    search.mockResolvedValue(answer());
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(emitted()["pick-session"]).toBeUndefined();
  });

  it("an arrow key during a composition leaves the highlight on the top row", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container } = mount([session("1", "foo", "/h/a/foo")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "ArrowDown", isComposing: true });
    expect(container.querySelector(".highlighted")?.getAttribute("data-kind")).toBe("session");
  });

  it("says nothing of no matches before the folder search has answered", async () => {
    search.mockReturnValue(new Promise(() => {}));
    const { container, queryAllByTestId } = mount([]);
    const input = container.querySelector("input") as HTMLInputElement;
    await fireEvent.update(input, "zz");
    const line = () => container.querySelector("[data-testid='box-search-list'] .search-loading > span:last-child")?.textContent;
    expect(line()).toBe("Searching...");
    await vi.advanceTimersByTimeAsync(300);
    expect(line()).toBe("Searching...");
    expect(queryAllByTestId("empty")).toHaveLength(0);
  });

  it("Enter does not pick a folder row that answers an older text", async () => {
    search.mockResolvedValueOnce(answer("/h/proj-a"));
    search.mockReturnValueOnce(new Promise(() => {}));
    const { container, emitted } = mount([]);
    const input = await typeAndSearch(container, "proj");
    await fireEvent.update(input, "projx");
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(emitted()["pick-folder"]).toBeUndefined();
    expect(rowsOf(container)).toEqual(["folder: /h/proj-a"]);
    expect(container.querySelector(".highlighted")).toBeNull();
  });

  it("Enter picks a session row while the folder rows answer an older text", async () => {
    search.mockResolvedValueOnce(answer("/h/proj-a"));
    search.mockReturnValueOnce(new Promise(() => {}));
    const { container, emitted } = mount([session("1", "projx", "/h/projx")]);
    const input = await typeAndSearch(container, "proj");
    await fireEvent.update(input, "projx");
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(emitted()["pick-session"]).toEqual([["projx"]]);
  });

  it("keeps the highlight on its row when a session is added above it", async () => {
    search.mockResolvedValue(answer());
    const { container, rerender, emitted } = mount([session("2", "foo-b", "/h/foo-b"), session("3", "foo-c", "/h/foo-c")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "ArrowDown" });
    await rerender({ sessions: [session("1", "foo-a", "/h/foo-a"), session("2", "foo-b", "/h/foo-b"), session("3", "foo-c", "/h/foo-c")] });
    expect(container.querySelector(".highlighted .result-name")?.textContent).toBe("foo-c");
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(emitted()["pick-session"]).toEqual([["foo-c"]]);
  });

  it("highlights the top row when the highlighted row is gone", async () => {
    search.mockResolvedValue(answer());
    const { container, rerender } = mount([session("1", "foo-a", "/h/foo-a"), session("2", "foo-b", "/h/foo-b"), session("3", "foo-c", "/h/foo-c")]);
    const input = await typeAndSearch(container, "foo");
    await fireEvent.keyDown(input, { key: "ArrowDown" });
    await fireEvent.keyDown(input, { key: "ArrowDown" });
    await rerender({ sessions: [session("1", "foo-a", "/h/foo-a"), session("2", "foo-b", "/h/foo-b")] });
    expect(container.querySelector(".highlighted .result-name")?.textContent).toBe("foo-a");
  });

  it("shows the failure line below the rows, so no row moves", async () => {
    search.mockRejectedValue(new ApiError(502, "unreachable", "build-box did not answer"));
    const { container } = mount([session("1", "foo", "/h/foo")]);
    await typeAndSearch(container, "foo");
    const children = Array.from(container.querySelector(".search-results")!.children);
    expect(children.map((el) => el.getAttribute("data-kind") ?? "line")).toEqual(["session", "line"]);
  });

  it("makes the row under the mouse the one highlighted row", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container } = mount([session("1", "foo", "/h/a/foo")]);
    await typeAndSearch(container, "foo");
    await fireEvent.mouseMove(container.querySelector(".search-result[data-kind='folder']")!);
    expect(Array.from(container.querySelectorAll(".highlighted")).map((el) => el.getAttribute("data-kind"))).toEqual(["folder"]);
  });

  it("says a session is running in the dot's label, as its title does", async () => {
    search.mockResolvedValue(answer());
    const { container } = mount([session("1", "foo", "/h/a/foo")]);
    await typeAndSearch(container, "foo");
    const dot = container.querySelector(".session-dot")!;
    expect(dot.getAttribute("aria-label")).toBe("Running");
    expect(dot.getAttribute("title")).toBe("Running");
  });

  it("picks the top row with Enter, moves with the arrows and closes with Escape keeping the text", async () => {
    search.mockResolvedValue(answer("/h/x/foo-two"));
    const { container, emitted } = mount([session("1", "foo", "/h/a/foo")]);
    const input = await typeAndSearch(container, "foo");

    await fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(container.querySelector(".highlighted")?.getAttribute("data-kind")).toBe("folder");
    await fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(container.querySelector(".highlighted")?.getAttribute("data-kind")).toBe("session");

    await fireEvent.keyDown(input, { key: "Escape" });
    expect(container.querySelector(".search-dropdown")).toBeNull();
    expect(input.value).toBe("foo");

    await fireEvent.update(input, "foo2");
    await fireEvent.update(input, "foo");
    await vi.advanceTimersByTimeAsync(300);
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(emitted()["pick-session"]).toEqual([["foo"]]);
  });
});
