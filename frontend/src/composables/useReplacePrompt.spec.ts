import { render, waitFor } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, type App } from "vue";

import { ApiError, networkError } from "@/api/apiError";
import { createI18n } from "@/i18n";
import { en } from "@/locales/en";
import { REPLACE_ARM_MS, useReplacePrompt, type FailedItem } from "./useReplacePrompt";

const { dialogSpy } = vi.hoisted(() => ({ dialogSpy: { warning: vi.fn() } }));

vi.mock("naive-ui", () => ({
  NButton: { props: ["size"], template: "<button><slot /></button>" },
  NCheckbox: { props: ["checked"], template: "<input type=\"checkbox\" />" },
  useDialog: () => dialogSpy,
}));

let i18n: ReturnType<typeof createI18n>;
// the real i18n, so the Japanese words can be read
vi.mock("@/i18n", async () => await vi.importActual("@/i18n"));

const i18nPlugin = { install(app: App) { i18n = createI18n(app); } };

let api: ReturnType<typeof useReplacePrompt>;
function mountComposable() {
  const Host = defineComponent({
    setup() {
      api = useReplacePrompt();
      return () => null;
    },
  });
  render(Host, { global: { plugins: [i18nPlugin] } });
  return api;
}

/** The nth dialog, as it is the moment it appears: its Replace buttons are still disabled. */
async function opened(n: number) {
  await waitFor(() => expect(dialogSpy.warning).toHaveBeenCalledTimes(n));
  return dialogSpy.warning.mock.calls[n - 1]![0];
}

/** The nth dialog once its Replace buttons are armed. */
async function prompt(n: number) {
  const options = await opened(n);
  vi.advanceTimersByTime(REPLACE_ARM_MS);
  return options;
}

/** The tick box of the prompt, when it draws one, and the buttons: label, state and click handler. */
function parts(options: any) {
  const root = options.action();
  const [row, box] = root.children as any[];
  return {
    box: box as { props: { checked: boolean; "onUpdate:checked": (v: boolean) => void }; children: { default(): string } } | null,
    rowStyle: row.props.style as string,
    buttons: (row.children as any[]).map((vnode) => ({
      label: vnode.children.default() as string,
      disabled: vnode.props.disabled as boolean,
      click: vnode.props.onClick as () => void,
    })),
  };
}
const buttons = (options: any) => parts(options).buttons;
const labelsOf = (options: any) => buttons(options).map((b) => b.label);
const press = (options: any, label: string) => buttons(options).find((b) => b.label === label)!.click();

const exists = (path: string): FailedItem => ({ path, error: "File already exists", reason: "exists" });

beforeEach(() => {
  // only the timers: waitFor keeps its own interval
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  dialogSpy.warning.mockImplementation(() => ({ destroy: vi.fn() }));
});

afterEach(() => {
  vi.useRealTimers();
  dialogSpy.warning.mockReset();
});

describe("useReplacePrompt: one name", () => {
  it("has the title, the sentence, three buttons and no tick box", async () => {
    const { askReplace } = mountComposable();
    const answer = askReplace("a.txt");
    const opts = await prompt(1);
    expect([opts.title, opts.content, opts.closable, opts.positiveText]).toEqual([
      "File already exists", "\"a.txt\" already exists here. Replace it?", false, undefined,
    ]);
    expect(parts(opts).box).toBeNull();
    expect(labelsOf(opts)).toEqual(["No, keep it", "Keep both", "Replace"]);
    press(opts, "Replace");
    expect(await answer).toBe("replace");
  });

  it.each([
    ["No, keep it", "no"],
    ["Keep both", "keep_both"],
    ["Replace", "replace"],
  ])("the button %s answers %s", async (label, expected) => {
    const { askReplace } = mountComposable();
    const answer = askReplace("a.txt");
    press(await prompt(1), label);
    expect(await answer).toBe(expected);
  });

  it.each([
    ["Escape", (o: any) => o.onEsc()],
    ["a mask click", (o: any) => o.onMaskClick()],
    ["the dialog leaving", (o: any) => o.onAfterLeave()],
  ])("%s answers no", async (_label, dismiss) => {
    const { askReplace } = mountComposable();
    const answer = askReplace("a.txt");
    dismiss(await prompt(1));
    expect(await answer).toBe("no");
  });

  it("the row of buttons never wraps and the dialog fits a 360 px screen", async () => {
    const { askReplace } = mountComposable();
    void askReplace("a.txt");
    const opts = await prompt(1);
    expect(parts(opts).rowStyle).toBe("display:flex;gap:8px;justify-content:flex-end;flex-wrap:nowrap");
    expect(opts.style).toBe("width:min(600px,calc(100vw - 32px))");
  });

  // Mutation killed: Keep both waiting for the arming delay, or Replace not waiting.
  it("only Replace is dead until 500 ms; Keep both and No work at once", async () => {
    const { askReplace } = mountComposable();
    let settled: string | null = null;
    void askReplace("a.txt").then((value) => { settled = value; });
    const options = await opened(1);
    expect(buttons(options).map((b) => b.disabled)).toEqual([false, false, true]);

    vi.advanceTimersByTime(100);
    press(options, "Replace");
    await Promise.resolve();
    expect(settled).toBeNull();

    vi.advanceTimersByTime(500);
    expect(buttons(options).map((b) => b.disabled)).toEqual([false, false, false]);
    press(options, "Replace");
    await waitFor(() => expect(settled).toBe("replace"));
  });

  it("a double click on Keep both is one answer", async () => {
    const { askReplace } = mountComposable();
    const answer = askReplace("a.txt");
    const opts = await opened(1);
    press(opts, "Keep both");
    press(opts, "Keep both");
    expect(await answer).toBe("keep_both");
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("the words in Japanese", async () => {
    const { askReplace } = mountComposable();
    i18n.setLocale("ja");
    try {
      void askReplace("a.txt");
      expect(labelsOf(await prompt(1))).toEqual(["いいえ（残す）", "両方残す", "置き換える"]);
    } finally {
      i18n.setLocale("en");
    }
  });
});

describe("useReplacePrompt: several names", () => {
  it("one prompt each, in order; three buttons and the tick box while more than one is left", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt", "c.txt"]);
    const first = await prompt(1);
    expect(first.content).toBe("\"a.txt\" already exists here. Replace it?");
    expect(labelsOf(first)).toEqual(["Skip", "Keep both", "Replace"]);
    expect(parts(first).box!.children.default()).toBe("Do the same for the other 2");
    press(first, "Replace");
    const second = await prompt(2);
    expect(parts(second).box!.children.default()).toBe("Do the same for the other 1");
    press(second, "Keep both");
    const third = await prompt(3);
    // the last name has nothing after it: no tick box, the one-name words
    expect(parts(third).box).toBeNull();
    expect(labelsOf(third)).toEqual(["No, keep it", "Keep both", "Replace"]);
    press(third, "No, keep it");
    expect(await answers).toEqual(["replace", "keep_both", "no"]);
  });

  it.each([
    ["Replace", "replace"],
    ["Keep both", "keep_both"],
    ["Skip", "no"],
  ])("%s with the box ticked answers the rest the same without another prompt", async (label, expected) => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt", "c.txt"]);
    const first = await prompt(1);
    parts(first).box!.props["onUpdate:checked"](true);
    press(first, label);
    expect(await answers).toEqual([expected, expected, expected]);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("the box ticked and then Escape answers no for that name only", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt"]);
    const first = await opened(1);
    parts(first).box!.props["onUpdate:checked"](true);
    first.onEsc();
    press(await prompt(2), "Keep both");
    expect(await answers).toEqual(["no", "keep_both"]);
  });

  it("the words in Japanese", async () => {
    const { askEach } = mountComposable();
    i18n.setLocale("ja");
    try {
      void askEach(["a.txt", "b.txt"]);
      const first = await prompt(1);
      expect(labelsOf(first)).toEqual(["スキップ", "両方残す", "置き換える"]);
      expect(parts(first).box!.children.default()).toBe("残りの 1 件にも適用");
    } finally {
      i18n.setLocale("en");
    }
  });
});

describe("useReplacePrompt: many names one request would replace", () => {
  it("the one-name dialog with the count and the first names, and Keep both", async () => {
    const { askReplaceMany } = mountComposable();
    const answer = askReplaceMany({ count: 12, names: ["a.txt", "sub/b.txt"] });
    const first = await prompt(1);
    expect([first.title, first.content]).toEqual([
      "File already exists",
      "Files here that would be replaced: 12 (a.txt, sub/b.txt, …). Replace them?",
    ]);
    expect(labelsOf(first)).toEqual(["No, keep it", "Keep both", "Replace"]);
    press(first, "Keep both");
    expect(await answer).toBe("keep_both");
  });

  it("in Japanese", async () => {
    const { askReplaceMany } = mountComposable();
    i18n.setLocale("ja");
    try {
      const answer = askReplaceMany({ count: 2, names: ["a.txt", "b.txt"] });
      const opts = await prompt(1);
      expect([opts.title, opts.content]).toEqual([
        "ファイルが既に存在します",
        "ここにある 2 個のファイルが置き換えられます（a.txt, b.txt）。置き換えますか？",
      ]);
      press(opts, "いいえ（残す）");
      expect(await answer).toBe("no");
    } finally {
      i18n.setLocale("en");
    }
  });
});

describe("useReplacePrompt: dismiss and leave", () => {
  it("dismiss answers the open prompt no and asks nothing more", async () => {
    const { askEach, dismiss } = mountComposable();
    const answers = askEach(["a.txt", "b.txt"]);
    await opened(1);
    dismiss();
    expect(await answers).toEqual(["no", "no"]);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("a view that is left answers every question no without a prompt", async () => {
    const { askReplace, leave } = mountComposable();
    leave();
    expect(await askReplace("a.txt")).toBe("no");
    expect(dialogSpy.warning).not.toHaveBeenCalled();
  });
});

describe("useReplacePrompt: withReplace", () => {
  const placing = (calls: Array<[string[], object]>) =>
    vi.fn(async (paths: string[], options: object) => {
      calls.push([paths, options]);
      if (!Object.keys(options).length) return { succeeded: ["/s/ok"], failed: paths.map(exists) };
      return { succeeded: paths, failed: [], ...("keepBoth" in options ? { placed: paths.map((p) => ({ path: p, placed: `${p} (2)` })) } : {}) };
    });

  it("Replace goes out with overwrite, Keep both with keepBoth, in two calls, never one with both", async () => {
    const { withReplace } = mountComposable();
    const calls: Array<[string[], object]> = [];
    const out = withReplace(["/s/a", "/s/b", "/s/c"], placing(calls));
    press(await prompt(1), "Replace");
    press(await prompt(2), "Keep both");
    press(await prompt(3), "No, keep it");
    expect(await out).toEqual({
      succeeded: ["/s/ok", "/s/a", "/s/b"],
      skipped: ["/s/c"],
      placed: [{ path: "/s/b", placed: "/s/b (2)" }],
      failed: [],
    });
    expect(calls).toEqual([
      [["/s/a", "/s/b", "/s/c"], {}],
      [["/s/a"], { overwrite: true }],
      [["/s/b"], { keepBoth: true }],
    ]);
  });

  // Mutation: the first retry's result is told only when everything is over.
  it("each retry's result is told before the next retry is sent", async () => {
    const { withReplace } = mountComposable();
    const log: unknown[] = [];
    const send = vi.fn(async (paths: string[], options: object) => {
      log.push(["send", paths, options]);
      if (!Object.keys(options).length) return { succeeded: [], failed: paths.map(exists) };
      if ("overwrite" in options) return { succeeded: [], failed: [{ path: "/s/a", error: "no", reason: "cannot_replace" as const }] };
      return { succeeded: paths, failed: [] };
    });
    const out = withReplace(["/s/a", "/s/b"], send, {
      onRetried: (succeeded, failed) => log.push(["retried", succeeded, failed.map((item) => item.path)]),
    });
    press(await prompt(1), "Replace");
    press(await prompt(2), "Keep both");
    await out;
    expect(log).toEqual([
      ["send", ["/s/a", "/s/b"], {}],
      ["send", ["/s/a"], { overwrite: true }],
      ["retried", [], ["/s/a"]],
      ["send", ["/s/b"], { keepBoth: true }],
      ["retried", ["/s/b"], []],
    ]);
  });

  it("the tick box on Keep both sends the rest in one keepBoth call", async () => {
    const { withReplace } = mountComposable();
    const calls: Array<[string[], object]> = [];
    const out = withReplace(["/s/a", "/s/b"], placing(calls));
    const first = await prompt(1);
    parts(first).box!.props["onUpdate:checked"](true);
    press(first, "Keep both");
    expect((await out).placed).toEqual([
      { path: "/s/a", placed: "/s/a (2)" },
      { path: "/s/b", placed: "/s/b (2)" },
    ]);
    expect(calls.map(([, options]) => options)).toEqual([{}, { keepBoth: true }]);
  });

  it("No to every prompt sends nothing more", async () => {
    const { withReplace } = mountComposable();
    const calls: Array<[string[], object]> = [];
    const out = withReplace(["/s/a", "/s/b"], placing(calls));
    const first = await prompt(1);
    parts(first).box!.props["onUpdate:checked"](true);
    press(first, "Skip");
    expect(await out).toEqual({ succeeded: ["/s/ok"], skipped: ["/s/a", "/s/b"], placed: [], failed: [] });
    expect(calls).toHaveLength(1);
  });

  it("a name that is not taken asks nothing", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async () => ({ succeeded: ["/s/a"], failed: [] }));
    expect(await withReplace(["/s/a"], send)).toEqual({ succeeded: ["/s/a"], skipped: [], placed: [], failed: [] });
    expect(dialogSpy.warning).not.toHaveBeenCalled();
  });

  it("a retry refused again says so under the reason failed and is not asked about again", async () => {
    const { withReplace } = mountComposable();
    const again: FailedItem = { path: "/s/b", error: "File already exists", reason: "exists" };
    const send = vi.fn(async (_p: string[], options: { keepBoth?: boolean }) =>
      options.keepBoth ? { succeeded: [], failed: [again] } : { succeeded: [], failed: [exists("/s/b")] });
    const out = withReplace(["/s/b"], send);
    press(await prompt(1), "Keep both");
    expect(await out).toEqual({ succeeded: [], skipped: [], placed: [], failed: [{ ...again, reason: "failed" }] });
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  // Mutation: the retry's failure is flattened to its message under the reason "failed".
  it("a Keep both retry the server refuses keeps the refusal's own reason and text", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async (_p: string[], options: { keepBoth?: boolean }) => {
      if (options.keepBoth) throw new ApiError(403, "denied", "Permission denied: /d/b");
      return { succeeded: [], failed: [exists("/s/b")] };
    });
    const out = withReplace(["/s/b"], send);
    press(await prompt(1), "Keep both");
    expect(await out).toEqual({
      succeeded: [],
      skipped: [],
      placed: [],
      failed: [{ path: "/s/b", error: "Permission denied: /d/b", reason: "denied" }],
    });
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("a retry that got no answer says so under the reason failed", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async (_p: string[], options: { overwrite?: boolean }) => {
      if (options.overwrite) throw networkError();
      return { succeeded: [], failed: [exists("/s/b")] };
    });
    const out = withReplace(["/s/b"], send);
    press(await prompt(1), "Replace");
    expect(await out).toEqual({
      succeeded: [],
      skipped: [],
      placed: [],
      failed: [{ path: "/s/b", error: en["error.reason.network"], reason: "failed" }],
    });
  });
});

describe("useReplacePrompt: inline mode", () => {
  function mountInline() {
    let inline!: ReturnType<typeof useReplacePrompt>;
    const Host = defineComponent({
      setup() {
        inline = useReplacePrompt({ inline: true });
        return () => null;
      },
    });
    render(Host, { global: { plugins: [i18nPlugin] } });
    return inline;
  }

  it("opens no dialog: the question is pendingPrompt, with the title, text and an arming delay", async () => {
    const { askReplace, pendingPrompt } = mountInline();
    const answer = askReplace("a.txt");
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(pendingPrompt.value).toMatchObject({
      title: "File already exists", content: "\"a.txt\" already exists here. Replace it?", remaining: 1, armed: false,
    });
    // Replace is dead until the delay is over: the press changes nothing.
    pendingPrompt.value!.answer("replace");
    expect(pendingPrompt.value).not.toBeNull();
    vi.advanceTimersByTime(REPLACE_ARM_MS);
    expect(pendingPrompt.value!.armed).toBe(true);
    pendingPrompt.value!.answer("replace");
    expect(await answer).toBe("replace");
    expect(pendingPrompt.value).toBeNull();
  });

  it("Keep both and No work before the delay is over", async () => {
    const { askReplace, pendingPrompt } = mountInline();
    const answer = askReplace("a.txt");
    pendingPrompt.value!.answer("keep_both");
    expect(await answer).toBe("keep_both");
  });

  it("the tick box on a press answers the rest; a question with one name left has none", async () => {
    const { askEach, pendingPrompt } = mountInline();
    const answers = askEach(["a", "b", "c"]);
    expect(pendingPrompt.value!.remaining).toBe(3);
    pendingPrompt.value!.same = true;
    pendingPrompt.value!.answer("no");
    expect(await answers).toEqual(["no", "no", "no"]);
    expect(pendingPrompt.value).toBeNull();
  });

  it("without the tick box the next name is asked, and dismiss answers no for all and clears it", async () => {
    const { askEach, pendingPrompt, dismiss } = mountInline();
    const answers = askEach(["a", "b"]);
    pendingPrompt.value!.answer("keep_both");
    await vi.waitFor(() => expect(pendingPrompt.value?.content).toContain("\"b\""));
    dismiss();
    expect(await answers).toEqual(["keep_both", "no"]);
    expect(pendingPrompt.value).toBeNull();
  });

  it("a view that is left asks nothing", async () => {
    const { askReplace, pendingPrompt, leave } = mountInline();
    leave();
    expect(await askReplace("a.txt")).toBe("no");
    expect(pendingPrompt.value).toBeNull();
  });

  it("the default mode still opens a dialog and has no pending question", async () => {
    const { askReplace, pendingPrompt } = mountComposable();
    void askReplace("a.txt");
    await opened(1);
    expect(pendingPrompt.value).toBeNull();
  });
});
