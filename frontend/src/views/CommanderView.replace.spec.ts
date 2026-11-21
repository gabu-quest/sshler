/**
 * The two-pane view asks "Replace?" for every name a transfer finds taken, on one box
 * and across boxes, and the progress dialog ends on each file's true state.
 */
import { render, fireEvent, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import { en } from "@/locales/en";
import * as http from "@/api/http";
import CommanderView from "./CommanderView.vue";
import { REPLACE_ARM_MS } from "@/composables/useReplacePrompt";

const { messageSpy, dialogSpy, reloads } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  dialogSpy: { warning: vi.fn() },
  reloads: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { box: "local", dir: "/src" }, path: "/commander", params: {}, name: "commander" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/api/http", () => ({
  sameBoxTransfer: vi.fn(),
  crossBoxTransfer: vi.fn(),
  batchDelete: vi.fn(),
  createFolder: vi.fn(),
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, basic_auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "local", host: "local", favorites: [], pinned: false, default_dir: "/" },
    { name: "other", host: "other", favorites: [], pinned: false, default_dir: "/" },
  ]),
}));

// A pane that lets the test choose the selection and counts reloads.
vi.mock("@/components/CommanderPane.vue", async () => {
  const { defineComponent, h } = await import("vue");
  return {
    default: defineComponent({
      props: ["box", "directory", "selected", "active", "token", "boxOptions", "favorites", "matchedFiles", "branchChangedFiles"],
      emits: ["update:selected", "update:box", "update:directory", "focus", "open-git-log", "open-blame"],
      setup(_props, { emit, expose }) {
        expose({ rows: [], getCursorFile: () => null, reload: reloads });
        return () => h("button", { class: "pick", onClick: () => emit("update:selected", ["/src/a.txt", "/src/b.txt", "/src/c.txt"]) }, "pick");
      },
    }),
  };
});
vi.mock("@/components/CommanderHotbar.vue", () => ({
  default: {
    emits: ["action"],
    template: '<div><button class="f5" @click="$emit(\'action\', \'F5\')">F5</button><button class="f6" @click="$emit(\'action\', \'F6\')">F6</button></div>',
  },
}));
const { passthrough } = vi.hoisted(() => ({
  passthrough: (name: string) => ({ default: { name, template: `<div class="stub-${name}" />` } }),
}));
vi.mock("@/components/CommanderBlame.vue", () => passthrough("CommanderBlame"));
vi.mock("@/components/CommanderDiffOverlay.vue", () => passthrough("CommanderDiffOverlay"));
vi.mock("@/components/CommanderGitLog.vue", () => passthrough("CommanderGitLog"));
vi.mock("@/components/CommanderHelpOverlay.vue", () => passthrough("CommanderHelpOverlay"));
vi.mock("@/components/FileEditorModal.vue", () => passthrough("FileEditorModal"));

vi.mock("naive-ui", () => ({
  NModal: { props: ["show", "maskClosable"], emits: ["update:show"], template: '<div v-if="show" class="stub-modal"><slot /></div>' },
  NButton: { props: ["type", "size", "ghost"], template: "<button><slot /></button>" },
  NProgress: { template: '<div class="stub-progress" />' },
  useDialog: () => dialogSpy,
  useMessage: () => messageSpy,
}));
vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return Object.fromEntries(["PhCheck", "PhHourglass", "PhSpinner", "PhWarning", "PhX", "PhMinus", "PhQuestion"].map((n) => [n, stub(n)]));
});

const i18nPlugin = { install(app: App) { createI18n(app); } };
const same = vi.mocked(http.sameBoxTransfer);
const cross = vi.mocked(http.crossBoxTransfer);
// What a person reads of a failed item: the line of its reason, then its text.
const line = (reason: string, text: string) => `${en[`error.reason.${reason}`]} — ${text}`;
const exists = (path: string) => ({ path, error: "File already exists", reason: "exists" as const });
const blocked = (path: string) => ({ path, error: "Only a regular file can be replaced", reason: "cannot_replace" as const });
const result = (succeeded: string[], failed: any[] = []) => ({ status: failed.length ? "partial" as const : "ok" as const, succeeded, failed });

/** What a cross-box call does: report this result once, as the stream's last event. */
function crossAnswers(...results: { succeeded: string[]; failed: any[] }[]) {
  const queue = [...results];
  cross.mockImplementation(((_s, _d, _p, _dest, _m, _t, onProgress, onDone) => {
    onProgress({ file: "/src/a.txt", bytes_done: 1, bytes_total: 2, index: 1, total: 3 });
    setTimeout(() => onDone(queue.shift()!), 0);
    return new AbortController();
  }) as typeof http.crossBoxTransfer);
}

async function mountReady(config: Record<string, string> = {}) {
  localStorage.setItem("sshler:commander:config", JSON.stringify(config));
  const utils = render(CommanderView, { global: { plugins: [i18nPlugin] } });
  await waitFor(() => expect(vi.mocked(http.fetchBoxes)).toHaveBeenCalled());
  return utils;
}

async function startTransfer(container: Element, key: "f5" | "f6") {
  await fireEvent.click(container.querySelector(".pick")!);
  await fireEvent.click(container.querySelector(`.${key}`)!);
}

async function prompt(n: number) {
  await waitFor(() => expect(dialogSpy.warning).toHaveBeenCalledTimes(n));
  // Replace is dead for a moment after a prompt appears; wait until it answers.
  await new Promise((resolve) => setTimeout(resolve, REPLACE_ARM_MS + 50));
  return dialogSpy.warning.mock.calls[n - 1]![0];
}

function press(options: any, label: string) {
  const row = options.action();
  (row.children as any[]).find((v) => v.children.default() === label).props.onClick();
}

/** The progress dialog's rows: name, state, text. */
async function finalRows(container: Element) {
  await waitFor(() => expect(container.querySelector(".transfer-footer button")?.textContent?.trim()).toBe("Close"));
  return [...container.querySelectorAll(".transfer-file-row")].map((row) => [
    row.querySelector(".file-name")!.textContent,
    [...row.classList].filter((c) => c !== "transfer-file-row")[0],
    row.querySelector(".file-error, .file-skipped")?.textContent?.trim() ?? null,
  ]);
}

describe("CommanderView: a transfer onto existing names asks", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
    same.mockReset();
    cross.mockReset();
  });

  // Mutation: the retry re-sends all three paths, or sends no flag, or never asks.
  it("one box: 3 files, the second exists, Replace: only that one is sent again, with overwrite", async () => {
    same
      .mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [exists("/src/b.txt")]))
      .mockResolvedValueOnce(result(["/src/b.txt"]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    const opts = await prompt(1);
    expect(opts.content).toBe("\"b.txt\" already exists here. Replace it?");
    opts.onPositiveClick();
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null], ["b.txt", "done", null], ["c.txt", "done", null],
    ]);
    expect(same.mock.calls.map((c) => [c[1], c[3], c[5]])).toEqual([
      [["/src/a.txt", "/src/b.txt", "/src/c.txt"], "copy", undefined],
      [["/src/b.txt"], "copy", { overwrite: true }],
    ]);
    expect(same.mock.calls[0]![2]).toBe("/dst");
  });

  // Mutation: a skipped file drawn as done, or the retry sent anyway.
  it("one box: No keeps the file and the dialog says so; nothing more is sent", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [exists("/src/b.txt")]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f6");
    (await prompt(1)).onNegativeClick();
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "skipped", "skipped, the existing file was kept"],
      ["c.txt", "done", null],
    ]);
    expect(same).toHaveBeenCalledTimes(1);
    expect(same.mock.calls[0]![3]).toBe("move");
    expect(reloads).toHaveBeenCalled();
  });

  // Mutation: asking about a name the server said cannot be replaced.
  it("an item that cannot be replaced shows the server's text and is not asked about", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [blocked("/src/b.txt")]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "failed", line("cannot_replace", "Only a regular file can be replaced")],
      ["c.txt", "done", null],
    ]);
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(same).toHaveBeenCalledTimes(1);
  });

  // Mutation: a retry error replaces the whole dialog with one error, or is asked about again.
  it("a retry that fails shows its text on that file and asks nothing more", async () => {
    same
      .mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [exists("/src/b.txt")]))
      .mockRejectedValueOnce(new Error("Could not replace /dst/b.txt: disk full"));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    (await prompt(1)).onPositiveClick();
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "failed", line("failed", "Could not replace /dst/b.txt: disk full")],
      ["c.txt", "done", null],
    ]);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  // Mutation: no "all" buttons with several names left, or Skip all applied to the first only.
  it("several taken names: Skip all leaves every one of them alone and sends nothing more", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt"], [exists("/src/b.txt"), exists("/src/c.txt")]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    press(await prompt(1), "Skip all");
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "skipped", "skipped, the existing file was kept"],
      ["c.txt", "skipped", "skipped, the existing file was kept"],
    ]);
    expect(same).toHaveBeenCalledTimes(1);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("several taken names: Replace all sends both again, once, and asks nothing more", async () => {
    same
      .mockResolvedValueOnce(result(["/src/a.txt"], [exists("/src/b.txt"), exists("/src/c.txt")]))
      .mockResolvedValueOnce(result(["/src/b.txt", "/src/c.txt"]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    press(await prompt(1), "Replace all");
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null], ["b.txt", "done", null], ["c.txt", "done", null],
    ]);
    expect(same.mock.calls.map((c) => [c[1], c[5]])).toEqual([
      [["/src/a.txt", "/src/b.txt", "/src/c.txt"], undefined],
      [["/src/b.txt", "/src/c.txt"], { overwrite: true }],
    ]);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("several taken names: Replace on the first, No on the last", async () => {
    same
      .mockResolvedValueOnce(result(["/src/a.txt"], [exists("/src/b.txt"), exists("/src/c.txt")]))
      .mockResolvedValueOnce(result(["/src/b.txt"]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    press(await prompt(1), "Replace");
    const second = await prompt(2);
    expect(second.content).toBe("\"c.txt\" already exists here. Replace it?");
    second.onNegativeClick();
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "done", null],
      ["c.txt", "skipped", "skipped, the existing file was kept"],
    ]);
  });

  // Mutation: the cross-box path never sends the flag.
  it("across boxes: Replace sends the accepted file again with overwrite and the dialog ends on done", async () => {
    crossAnswers(
      { succeeded: ["/src/a.txt", "/src/c.txt"], failed: [exists("/src/b.txt")] },
      { succeeded: ["/src/b.txt"], failed: [] },
    );
    const { container } = await mountReady({ rightBox: "other", rightDir: "/dst" });
    await startTransfer(container, "f5");
    (await prompt(1)).onPositiveClick();
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null], ["b.txt", "done", null], ["c.txt", "done", null],
    ]);
    expect(cross.mock.calls.map((c) => [c[0], c[1], c[2], c[3], c[4], c[9]?.overwrite])).toEqual([
      ["local", "other", ["/src/a.txt", "/src/b.txt", "/src/c.txt"], "/dst", "copy", undefined],
      ["local", "other", ["/src/b.txt"], "/dst", "copy", true],
    ]);
  });

  it("across boxes: No keeps the file and sends nothing more", async () => {
    crossAnswers({ succeeded: ["/src/a.txt", "/src/c.txt"], failed: [exists("/src/b.txt")] });
    const { container } = await mountReady({ rightBox: "other", rightDir: "/dst" });
    await startTransfer(container, "f6");
    (await prompt(1)).onEsc();
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "skipped", "skipped, the existing file was kept"],
      ["c.txt", "done", null],
    ]);
    expect(cross).toHaveBeenCalledTimes(1);
  });
});

/** The rows as they read right now: name, state, text. */
function rowsNow(container: Element) {
  return [...container.querySelectorAll(".transfer-file-row")].map((row) => [
    row.querySelector(".file-name")!.textContent,
    [...row.classList].filter((c) => c !== "transfer-file-row")[0],
    row.querySelector(".file-error, .file-skipped, .file-waiting")?.textContent?.trim() ?? null,
  ]);
}

describe("CommanderView: every row is true while the transfer runs", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
    same.mockReset();
    cross.mockReset();
    dialogSpy.warning.mockReset();
  });

  // Mutation: the file asked about reads done (or pending) while the prompt is open.
  it("a file waiting for the answer says so; the answer shows at once, before the end", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [exists("/src/b.txt")]));
    let release: (value: any) => void = () => {};
    same.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    const opts = await prompt(1);
    expect(rowsNow(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "waiting", "waiting for your answer"],
      ["c.txt", "done", null],
    ]);
    opts.onPositiveClick();
    await waitFor(() => expect(same).toHaveBeenCalledTimes(2));
    // the retry is running: the files the first call placed stay done, only b is pending
    expect(rowsNow(container)).toEqual([
      ["a.txt", "done", null], ["b.txt", "pending", null], ["c.txt", "done", null],
    ]);
    release(result(["/src/b.txt"]));
    expect(await finalRows(container)).toEqual([
      ["a.txt", "done", null], ["b.txt", "done", null], ["c.txt", "done", null],
    ]);
  });

  it("a skipped file reads skipped as soon as the user said so, while another is still being asked", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt"], [exists("/src/b.txt"), exists("/src/c.txt")]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    (await prompt(1)).onEsc();
    await prompt(2);
    expect(rowsNow(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "skipped", "skipped, the existing file was kept"],
      ["c.txt", "waiting", "waiting for your answer"],
    ]);
  });

  it("a file the first call failed reads failed during the retry", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt"], [exists("/src/b.txt"), blocked("/src/c.txt")]));
    same.mockImplementationOnce(() => new Promise(() => {}));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    (await prompt(1)).onPositiveClick();
    await waitFor(() => expect(same).toHaveBeenCalledTimes(2));
    expect(rowsNow(container)).toEqual([
      ["a.txt", "done", null],
      ["b.txt", "pending", null],
      ["c.txt", "failed", line("cannot_replace", "Only a regular file can be replaced")],
    ]);
  });

  // Mutation: a file the server refused reads done while the files after it are copied.
  it("across boxes: a file the server refused is not done while the call goes on", async () => {
    let finish: (value: any) => void = () => {};
    cross.mockImplementationOnce(((_s, _d, _p, _dest, _m, _t, onProgress, onDone, _onError, options) => {
      options!.onItemFailed!(exists("/src/a.txt"));
      options!.onItemFailed!(blocked("/src/b.txt"));
      onProgress({ file: "/src/c.txt", bytes_done: 1, bytes_total: 2, index: 2, total: 3 });
      finish = onDone;
      return new AbortController();
    }) as typeof http.crossBoxTransfer);
    const { container } = await mountReady({ rightBox: "other", rightDir: "/dst" });
    await startTransfer(container, "f5");
    await waitFor(() => expect(rowsNow(container)).toEqual([
      ["a.txt", "pending", null],
      ["b.txt", "failed", line("cannot_replace", "Only a regular file can be replaced")],
      ["c.txt", "active", null],
    ]));
    finish({ succeeded: ["/src/c.txt"], failed: [exists("/src/a.txt"), blocked("/src/b.txt")] });
    await prompt(1);
    expect(rowsNow(container)).toEqual([
      ["a.txt", "waiting", "waiting for your answer"],
      ["b.txt", "failed", line("cannot_replace", "Only a regular file can be replaced")],
      ["c.txt", "done", null],
    ]);
  });

  it("a retry cancelled half way keeps the first call's files done", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [exists("/src/b.txt")]));
    const { container } = await mountReady({ rightDir: "/dst" });
    same.mockImplementationOnce(() => new Promise(() => {}));
    await startTransfer(container, "f5");
    (await prompt(1)).onPositiveClick();
    await waitFor(() => expect(same).toHaveBeenCalledTimes(2));
    await fireEvent.click(container.querySelector(".transfer-footer button")!);
    expect(rowsNow(container)).toEqual([
      ["a.txt", "done", null], ["b.txt", "pending", null], ["c.txt", "done", null],
    ]);
  });
});

describe("CommanderView: a prompt does not outlive what it belongs to", () => {
  const destroy = vi.fn();
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    destroy.mockReset();
    dialogSpy.warning.mockImplementation(() => ({ destroy }));
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
    same.mockReset();
    cross.mockReset();
    dialogSpy.warning.mockReset();
  });

  // Mutation: the prompt stays up after the view is left, and a Yes starts a retry.
  it("leaving the view takes the prompt down and a late Yes sends nothing", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [exists("/src/b.txt")]));
    const { container, unmount } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    const opts = await prompt(1);
    unmount();
    expect(destroy).toHaveBeenCalledTimes(1);
    opts.onPositiveClick();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(same).toHaveBeenCalledTimes(1);
  });

  // Mutation: Cancel writes "Cancelled" but a later Yes still starts the retry.
  it("Cancel with a prompt open takes it down; no retry is sent", async () => {
    same.mockResolvedValueOnce(result(["/src/a.txt", "/src/c.txt"], [exists("/src/b.txt")]));
    const { container } = await mountReady({ rightDir: "/dst" });
    await startTransfer(container, "f5");
    const opts = await prompt(1);
    await fireEvent.click(container.querySelector(".transfer-footer button")!);
    expect(destroy).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".transfer-error")?.textContent).toContain("Cancelled");
    opts.onPositiveClick();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(same).toHaveBeenCalledTimes(1);
  });
});
