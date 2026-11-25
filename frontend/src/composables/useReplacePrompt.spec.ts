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

/** The dialog instance the mock handed back for the nth call. */
function instanceOf(n: number): { positiveButtonProps?: { disabled?: boolean }; destroy: ReturnType<typeof vi.fn> } {
  return dialogSpy.warning.mock.results[n - 1]!.value;
}

/** The buttons the multi-name prompt draws: label and click handler, in order. */
function buttons(options: any) {
  const row = options.action();
  return (row.children as any[]).map((vnode) => ({
    label: vnode.children.default(),
    click: vnode.props.onClick as () => void,
  }));
}

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

describe("useReplacePrompt", () => {
  it("one name: the prompt has the two buttons, the title and the sentence the upload showed", async () => {
    const { askReplace } = mountComposable();
    const answer = askReplace("a.txt");
    const opts = await prompt(1);
    expect([opts.title, opts.content, opts.positiveText, opts.negativeText, opts.closable, opts.action]).toEqual([
      "File already exists", "\"a.txt\" already exists here. Replace it?", "Replace", "No, keep it", false, undefined,
    ]);
    opts.onPositiveClick();
    expect(await answer).toBe(true);
  });

  it.each([
    ["No", (o: any) => o.onNegativeClick()],
    ["Escape", (o: any) => o.onEsc()],
    ["a mask click", (o: any) => o.onMaskClick()],
    ["the dialog leaving", (o: any) => o.onAfterLeave()],
  ])("one name: %s answers no", async (_label, dismiss) => {
    const { askReplace } = mountComposable();
    const answer = askReplace("a.txt");
    dismiss(await prompt(1));
    expect(await answer).toBe(false);
  });

  it("several names: one prompt each, in order, with Replace all and Skip all offered while names remain", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt", "c.txt"]);
    const first = await prompt(1);
    expect(first.content).toBe("\"a.txt\" already exists here. Replace it?");
    expect(buttons(first).map((b) => b.label)).toEqual(["No, keep it", "Replace", "Skip all", "Replace all"]);
    buttons(first)[1]!.click();
    const second = await prompt(2);
    expect(second.content).toBe("\"b.txt\" already exists here. Replace it?");
    buttons(second)[0]!.click();
    const third = await prompt(3);
    // the last name has nothing after it: the two buttons only
    expect(third.content).toBe("\"c.txt\" already exists here. Replace it?");
    expect(third.action).toBeUndefined();
    third.onPositiveClick();
    expect(await answers).toEqual([true, false, true]);
  });

  it("Replace all answers yes to the rest without another prompt", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt", "c.txt"]);
    buttons(await prompt(1))[3]!.click();
    expect(await answers).toEqual([true, true, true]);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("Skip all answers no to the rest without another prompt", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt", "c.txt"]);
    buttons(await prompt(1))[1]!.click();
    buttons(await prompt(2))[2]!.click();
    expect(await answers).toEqual([true, false, false]);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(2);
  });

  it("Escape in the middle counts as no for that name only", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt"]);
    (await prompt(1)).onEsc();
    (await prompt(2)).onPositiveClick();
    expect(await answers).toEqual([false, true]);
  });

  it("the Japanese words are used for the two new buttons", async () => {
    const { askEach } = mountComposable();
    i18n.setLocale("ja");
    try {
      void askEach(["a.txt", "b.txt"]);
      expect(buttons(await prompt(1)).map((b) => b.label)).toEqual([
        "いいえ（残す）", "置き換える", "すべてスキップ", "すべて置き換える",
      ]);
    } finally {
      i18n.setLocale("en");
    }
  });
});

describe("a prompt that has just appeared", () => {
  // Mutation killed: no delay, so the second click of a double click answers the next dialog.
  it("two buttons: Replace is disabled and a click at 100 ms changes nothing; at 600 ms it answers", async () => {
    const { askReplace } = mountComposable();
    let settled: boolean | null = null;
    void askReplace("a.txt").then((value) => { settled = value; });
    const options = await opened(1);
    expect(options.positiveButtonProps).toEqual({ disabled: true });

    vi.advanceTimersByTime(100);
    expect(options.onPositiveClick()).toBe(false); // false keeps the dialog open
    await Promise.resolve();
    expect(settled).toBeNull();
    expect(instanceOf(1).destroy).not.toHaveBeenCalled();

    vi.advanceTimersByTime(500);
    expect(instanceOf(1).positiveButtonProps).toEqual({ disabled: false });
    options.onPositiveClick();
    await waitFor(() => expect(settled).toBe(true));
  });

  it("four buttons: Replace and Replace all are disabled and dead until 500 ms; the others work at once", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt"]);
    const options = await opened(1);
    const disabledOf = () => (options.action().children as any[]).map((v) => v.props.disabled);
    expect(disabledOf()).toEqual([false, true, false, true]);

    vi.advanceTimersByTime(100);
    buttons(options)[1]!.click(); // Replace
    buttons(options)[3]!.click(); // Replace all
    await Promise.resolve();
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
    expect(instanceOf(1).destroy).not.toHaveBeenCalled();

    vi.advanceTimersByTime(500);
    expect(disabledOf()).toEqual([false, false, false, false]);
    buttons(options)[1]!.click();
    (await opened(2)).onNegativeClick();
    expect(await answers).toEqual([true, false]);
  });

  it.each([
    ["No", (o: any) => o.onNegativeClick()],
    ["Escape", (o: any) => o.onEsc()],
    ["a mask click", (o: any) => o.onMaskClick()],
  ])("%s works at once", async (_label, dismiss) => {
    const { askReplace } = mountComposable();
    const answer = askReplace("a.txt");
    dismiss(await opened(1));
    expect(await answer).toBe(false);
  });

  it("Skip all works at once", async () => {
    const { askEach } = mountComposable();
    const answers = askEach(["a.txt", "b.txt", "c.txt"]);
    buttons(await opened(1))[2]!.click();
    expect(await answers).toEqual([false, false, false]);
  });
});

describe("dismiss", () => {
  it("answers No for the open prompt and every name still to ask, and takes the dialog down", async () => {
    const { askEach, dismiss } = mountComposable();
    const answers = askEach(["a.txt", "b.txt", "c.txt"]);
    await prompt(1);
    dismiss();
    expect(await answers).toEqual([false, false, false]);
    expect(instanceOf(1).destroy).toHaveBeenCalledTimes(1);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("a Yes given before the dismissal is not sent: no retry goes out", async () => {
    const { withReplace, dismiss } = mountComposable();
    const send = vi.fn(async () => ({ succeeded: [], failed: [exists("/s/a"), exists("/s/b")] }));
    const out = withReplace(["/s/a", "/s/b"], send);
    buttons(await prompt(1))[1]!.click(); // a: yes
    await prompt(2);
    dismiss();
    expect(await out).toEqual({ succeeded: [], skipped: ["/s/a", "/s/b"], failed: [] });
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("dismissed while the first call is still running: nothing is asked and nothing is sent again", async () => {
    const { withReplace, dismiss } = mountComposable();
    let release: (value: { succeeded: string[]; failed: FailedItem[] }) => void = () => {};
    const send = vi.fn(() => new Promise<{ succeeded: string[]; failed: FailedItem[] }>((resolve) => { release = resolve; }));
    const out = withReplace(["/s/a"], send);
    dismiss();
    release({ succeeded: [], failed: [exists("/s/a")] });
    expect(await out).toEqual({ succeeded: [], skipped: ["/s/a"], failed: [] });
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("an operation that starts after a dismissal asks as usual", async () => {
    const { askReplace, dismiss } = mountComposable();
    dismiss();
    const answer = askReplace("a.txt");
    (await prompt(1)).onPositiveClick();
    expect(await answer).toBe(true);
  });
});

describe("leave", () => {
  // Mutation: leave only dismisses, so the next taken name of a running upload asks again.
  it("takes the open prompt down, and a question asked afterwards is No without a prompt", async () => {
    const { askReplace, leave } = mountComposable();
    const first = askReplace("a.txt");
    await prompt(1);
    leave();
    expect(await first).toBe(false);
    expect(instanceOf(1).destroy).toHaveBeenCalledTimes(1);
    expect(await askReplace("b.txt")).toBe(false);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
  });

  it("a batch that comes back after the view was left asks nothing and sends nothing again", async () => {
    const { withReplace, leave } = mountComposable();
    leave();
    const send = vi.fn(async () => ({ succeeded: [], failed: [exists("/s/a")] }));
    expect(await withReplace(["/s/a"], send)).toEqual({ succeeded: [], skipped: ["/s/a"], failed: [] });
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(send).toHaveBeenCalledTimes(1);
  });
});

describe("withReplace", () => {
  it("sends once and returns the result when nothing exists", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async (_paths: string[], _overwrite: boolean) => ({ succeeded: ["/d/a"], failed: [] }));
    const out = await withReplace(["/d/a"], send);
    expect(send.mock.calls).toEqual([[["/d/a"], false]]);
    expect(out).toEqual({ succeeded: ["/d/a"], skipped: [], failed: [] });
    expect(dialogSpy.warning).not.toHaveBeenCalled();
  });

  it("asks for each existing name, then sends the accepted ones again, once, with overwrite", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async (paths: string[], overwrite: boolean) =>
      overwrite
        ? { succeeded: paths, failed: [] }
        : { succeeded: ["/s/a"], failed: [exists("/s/b"), exists("/s/c"), exists("/s/d")] });
    const out = withReplace(["/s/a", "/s/b", "/s/c", "/s/d"], send);
    buttons(await prompt(1))[0]!.click(); // b: no
    const second = await prompt(2);
    expect(second.content).toBe("\"c\" already exists here. Replace it?");
    buttons(second)[1]!.click(); // c: yes
    (await prompt(3)).onPositiveClick(); // d: yes
    const result = await out;
    expect(send.mock.calls).toEqual([
      [["/s/a", "/s/b", "/s/c", "/s/d"], false],
      [["/s/c", "/s/d"], true],
    ]);
    expect(result).toEqual({ succeeded: ["/s/a", "/s/c", "/s/d"], skipped: ["/s/b"], failed: [] });
  });

  it("an item that cannot be replaced is not asked about and keeps the server's text", async () => {
    const { withReplace } = mountComposable();
    const blocked: FailedItem = { path: "/s/x", error: "Only a regular file can be replaced", reason: "cannot_replace" };
    const send = vi.fn(async () => ({ succeeded: [], failed: [blocked, exists("/s/y")] }));
    const out = withReplace(["/s/x", "/s/y"], send);
    (await prompt(1)).onNegativeClick();
    expect(await out).toEqual({ succeeded: [], skipped: ["/s/y"], failed: [blocked] });
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("No to every prompt sends nothing more", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async () => ({ succeeded: [], failed: [exists("/s/a"), exists("/s/b")] }));
    const out = withReplace(["/s/a", "/s/b"], send);
    buttons(await prompt(1))[2]!.click();
    expect(await out).toEqual({ succeeded: [], skipped: ["/s/a", "/s/b"], failed: [] });
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("a retry refused again, or one that throws, shows the server's text and is not asked about again", async () => {
    const { withReplace } = mountComposable();
    const again: FailedItem = { path: "/s/b", error: "File already exists", reason: "exists" };
    const send = vi.fn(async (_p: string[], overwrite: boolean) =>
      overwrite ? { succeeded: [], failed: [again] } : { succeeded: [], failed: [exists("/s/b")] });
    const out = withReplace(["/s/b"], send);
    (await prompt(1)).onPositiveClick();
    expect(await out).toEqual({ succeeded: [], skipped: [], failed: [{ ...again, reason: "failed" }] });
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);

    dialogSpy.warning.mockReset();
    const boom = vi.fn(async (_p: string[], overwrite: boolean) => {
      if (overwrite) throw new Error("Could not replace /s/b: disk full");
      return { succeeded: ["/s/a"], failed: [exists("/s/b")] };
    });
    const out2 = withReplace(["/s/a", "/s/b"], boom);
    (await prompt(1)).onPositiveClick();
    expect(await out2).toEqual({
      succeeded: ["/s/a"],
      skipped: [],
      failed: [{ path: "/s/b", error: "Could not replace /s/b: disk full", reason: "failed" }],
    });
  });

  // Mutation: the retry's failure is flattened to its message under the reason "failed".
  it("a retry the server refuses keeps the refusal's own reason and text", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async (_p: string[], overwrite: boolean) => {
      if (overwrite) throw new ApiError(403, "denied", "Permission denied: /d/b");
      return { succeeded: [], failed: [exists("/s/b")] };
    });
    const out = withReplace(["/s/b"], send);
    (await prompt(1)).onPositiveClick();
    expect(await out).toEqual({
      succeeded: [],
      skipped: [],
      failed: [{ path: "/s/b", error: "Permission denied: /d/b", reason: "denied" }],
    });
  });

  it("a retry that got no answer says so under the reason failed", async () => {
    const { withReplace } = mountComposable();
    const send = vi.fn(async (_p: string[], overwrite: boolean) => {
      if (overwrite) throw networkError();
      return { succeeded: [], failed: [exists("/s/b")] };
    });
    const out = withReplace(["/s/b"], send);
    (await prompt(1)).onPositiveClick();
    expect(await out).toEqual({
      succeeded: [],
      skipped: [],
      failed: [{ path: "/s/b", error: en["error.reason.network"], reason: "failed" }],
    });
  });
});
