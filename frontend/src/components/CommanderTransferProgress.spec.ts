import { fireEvent, render } from "@testing-library/vue";
import { nextTick, reactive } from "vue";
import { describe, expect, it, vi } from "vitest";

import CommanderTransferProgress from "./CommanderTransferProgress.vue";
import { en } from "@/locales/en";

// What a person reads of an item the server refused: the line of its reason, then its text.
const refused = `${en["error.reason.cannot_replace"]} — Only a regular file can be replaced`;

vi.mock("naive-ui", () => ({
  NModal: { props: ["show"], template: '<div v-if="show" class="stub-modal"><slot /></div>' },
  NButton: { template: "<button><slot /></button>" },
  NCheckbox: { props: ["checked"], emits: ["update:checked"], template: '<label><input type="checkbox" :checked="checked" @change="$emit(\'update:checked\', $event.target.checked)" /><slot /></label>' },
  NProgress: { template: '<div class="stub-progress" />' },
}));
vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return Object.fromEntries(["PhCheck", "PhHourglass", "PhSpinner", "PhWarning", "PhX", "PhMinus", "PhQuestion"].map((n) => [n, stub(n)]));
});

const base = {
  show: true, mode: "move" as const, srcBox: "a", destBox: "b", destination: "/d",
  files: ["/s/one.txt", "/s/two.txt", "/s/three.txt"], progress: null, error: null,
};

/** Each row's file name, state class, icon and the extra text it shows. */
function rows(container: Element) {
  return [...container.querySelectorAll(".transfer-file-row")].map((row) => ({
    name: row.querySelector(".file-name")!.textContent,
    state: [...row.classList].filter((c) => c !== "transfer-file-row"),
    icon: row.querySelector("[data-icon]")!.getAttribute("data-icon"),
    note: row.querySelector(".file-error, .file-skipped, .file-waiting")?.textContent?.trim() ?? null,
  }));
}

describe("CommanderTransferProgress final states", () => {
  // Mutation: a skipped file drawn as done (the check mark) or as failed (red, with no text).
  it("shows each file as done, kept as it was, or failed with the server's text", () => {
    const { container } = render(CommanderTransferProgress, {
      props: {
        ...base,
        result: {
          succeeded: ["/s/one.txt"],
          failed: [{ path: "/s/three.txt", error: "Only a regular file can be replaced", reason: "cannot_replace" }],
        },
        skipped: ["/s/two.txt"],
      },
    });
    expect(rows(container)).toEqual([
      { name: "one.txt", state: ["done"], icon: "PhCheck", note: null },
      { name: "two.txt", state: ["skipped"], icon: "PhMinus", note: "skipped, the existing file was kept" },
      { name: "three.txt", state: ["failed"], icon: "PhX", note: refused },
    ]);
    // Mutation: the count says every file once the transfer has ended.
    expect(container.querySelector(".overall-label")!.textContent).toBe("1 / 3 files");
  });

  it("without a skipped list every file that did not fail is done, as before", () => {
    const { container } = render(CommanderTransferProgress, {
      props: { ...base, result: { succeeded: base.files, failed: [] } },
    });
    expect(rows(container).map((r) => r.state)).toEqual([["done"], ["done"], ["done"]]);
  });
});

describe("CommanderTransferProgress while a transfer runs", () => {
  const first = { file: "/s/two.txt", bytes_done: 1, bytes_total: 2, index: 0, total: 1 };

  // Mutation: the files an earlier call placed read pending again during the retry.
  it("files placed, skipped, failed or waiting keep that state under the progress of a retry", () => {
    const { container } = render(CommanderTransferProgress, {
      props: {
        ...base,
        files: ["/s/one.txt", "/s/two.txt", "/s/three.txt", "/s/four.txt"],
        progress: first,
        result: null,
        placed: ["/s/one.txt"],
        skipped: ["/s/three.txt"],
        failedSoFar: [{ path: "/s/four.txt", error: "Only a regular file can be replaced", reason: "cannot_replace" }],
        sending: ["/s/two.txt"],
      },
    });
    expect(rows(container).map((r) => [r.name, r.state[0], r.note])).toEqual([
      ["one.txt", "done", null],
      ["two.txt", "active", null],
      ["three.txt", "skipped", "skipped, the existing file was kept"],
      ["four.txt", "failed", refused],
    ]);
    // Mutation: the count is the retry's own index (0), not the files placed so far.
    expect(container.querySelector(".overall-label")!.textContent).toBe("1 / 4 files");
  });

  it("a file waiting for the user's answer has its own icon and text", () => {
    const { container } = render(CommanderTransferProgress, {
      props: { ...base, result: null, placed: ["/s/one.txt", "/s/three.txt"], waiting: ["/s/two.txt"] },
    });
    expect(rows(container)).toEqual([
      { name: "one.txt", state: ["done"], icon: "PhCheck", note: null },
      { name: "two.txt", state: ["waiting"], icon: "PhQuestion", note: "waiting for your answer" },
      { name: "three.txt", state: ["done"], icon: "PhCheck", note: null },
    ]);
  });

  // Mutation: a file the server did not place reads done once the call moves on past it.
  it("a file the call in flight refused is not done while the files after it copy", () => {
    const { container } = render(CommanderTransferProgress, {
      props: {
        ...base,
        result: null,
        held: ["/s/one.txt"],
        failedSoFar: [{ path: "/s/two.txt", error: "Only a regular file can be replaced", reason: "cannot_replace" }],
        progress: { file: "/s/three.txt", bytes_done: 1, bytes_total: 2, index: 2, total: 3 },
      },
    });
    expect(rows(container).map((r) => [r.name, r.state[0], r.note])).toEqual([
      ["one.txt", "pending", null],
      ["two.txt", "failed", refused],
      ["three.txt", "active", null],
    ]);
  });

  // Mutation: the progress index of a retry (counted in the files it carries) is read against all files.
  it("the progress index counts within the files being sent", () => {
    const { container } = render(CommanderTransferProgress, {
      props: {
        ...base,
        result: null,
        placed: ["/s/one.txt"],
        sending: ["/s/two.txt", "/s/three.txt"],
        progress: { file: "/s/three.txt", bytes_done: 1, bytes_total: 2, index: 1, total: 2 },
      },
    });
    expect(rows(container).map((r) => [r.name, r.state[0]])).toEqual([
      ["one.txt", "done"], ["two.txt", "done"], ["three.txt", "active"],
    ]);
  });
});

describe("CommanderTransferProgress with a question open", () => {
  const answered: string[] = [];
  const question = (remaining = 1, armed = true) =>
    reactive({
      title: "File already exists", content: "\"two.txt\" already exists here. Replace it?",
      remaining, armed, same: false, answer: (choice: string) => void answered.push(choice),
    });
  const open = (prompt: ReturnType<typeof question>) =>
    render(CommanderTransferProgress, { props: { ...base, result: null, waiting: ["/s/two.txt"], prompt: prompt as never } });

  // Mutation: the question is drawn over the list (a dialog of its own) or not drawn by this component.
  it("draws the question as a dialog under the file list, which keeps every row", () => {
    answered.length = 0;
    const { container } = open(question());
    const panel = container.querySelector(".transfer-prompt")!;
    expect(panel.getAttribute("role")).toBe("dialog");
    expect(panel.textContent).toContain("File already exists");
    expect(panel.textContent).toContain("\"two.txt\" already exists here. Replace it?");
    const list = container.querySelector(".transfer-files")!;
    expect(list.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelectorAll(".transfer-file-row")).toHaveLength(3);
    expect([...panel.querySelectorAll(".replace-actions button")].map((b) => b.textContent!.trim())).toEqual([
      "No, keep it", "Keep both", "Replace",
    ]);
    expect(panel.querySelector("input[type=checkbox]")).toBeNull();
  });

  it("each button gives its answer; Replace is disabled until armed", async () => {
    answered.length = 0;
    const prompt = question(1, false);
    const { container } = open(prompt);
    const [no, both, replace] = [...container.querySelectorAll<HTMLButtonElement>(".replace-actions button")];
    expect(replace!.disabled).toBe(true);
    await fireEvent.click(no!);
    await fireEvent.click(both!);
    prompt.armed = true;
    await nextTick();
    expect(replace!.disabled).toBe(false);
    await fireEvent.click(replace!);
    expect(answered).toEqual(["no", "keep_both", "replace"]);
  });

  it("with more names left it offers Skip and the tick box, which sets `same`", async () => {
    const prompt = question(3);
    const { container } = open(prompt);
    expect(container.querySelector(".replace-actions button")!.textContent!.trim()).toBe("Skip");
    await fireEvent.click(container.querySelector("input[type=checkbox]")!);
    expect(prompt.same).toBe(true);
    expect(container.querySelector(".transfer-prompt")!.textContent).toContain("Do the same for the other 2");
  });

  // Mutation: Escape closes the whole transfer instead of answering No.
  it("Escape answers no and does not close the transfer", async () => {
    answered.length = 0;
    const { container, emitted } = open(question());
    await fireEvent.keyDown(container.querySelector(".transfer-prompt")!, { key: "Escape" });
    expect(answered).toEqual(["no"]);
    expect(emitted().close).toBeUndefined();
  });

  it("Escape with the focus on another part of the dialog answers no once", async () => {
    answered.length = 0;
    const { container, emitted } = open(question());
    await fireEvent.keyDown(container.querySelector(".transfer-files")!, { key: "Escape" });
    expect(answered).toEqual(["no"]);
    expect(emitted().close).toBeUndefined();
  });

  it("Escape with no question open is left to the modal", async () => {
    answered.length = 0;
    const { container } = open(null as never);
    const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    container.querySelector(".transfer-files")!.dispatchEvent(event);
    expect(answered).toEqual([]);
    expect(event.defaultPrevented).toBe(false);
  });

  it("a click after the question closed does nothing and does not throw", async () => {
    const { container, rerender } = open(question());
    const replace = [...container.querySelectorAll<HTMLButtonElement>(".replace-actions button")][2]!;
    await rerender({ prompt: null });
    expect(container.querySelector(".transfer-prompt")).toBeNull();
    expect(() => replace.click()).not.toThrow();
  });

  it("the title and the count read through the texts, in both words of a move and a copy", () => {
    const { container } = render(CommanderTransferProgress, { props: { ...base, result: null } });
    expect(container.querySelector(".transfer-title")!.textContent).toBe("Move 3 file(s) → b");
    expect(container.querySelector(".overall-label")!.textContent).toBe("0 / 3 files");
  });
});

describe("CommanderTransferProgress colours", () => {
  // Mutation: a hard-coded dark colour comes back and the light theme is unreadable.
  it("the style uses the theme tokens only", async () => {
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(`${process.cwd()}/src/components/CommanderTransferProgress.vue`, "utf8");
    const css = source.slice(source.indexOf("<style"));
    expect(css.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g)).toBeNull();
    expect(css).not.toContain("@keyframes spin");
    expect(source).not.toMatch(/color="#/);
  });
});
