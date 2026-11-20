import { render } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";

import CommanderTransferProgress from "./CommanderTransferProgress.vue";

vi.mock("naive-ui", () => ({
  NModal: { props: ["show"], template: '<div v-if="show" class="stub-modal"><slot /></div>' },
  NButton: { template: "<button><slot /></button>" },
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
      { name: "three.txt", state: ["failed"], icon: "PhX", note: "Only a regular file can be replaced" },
    ]);
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
      ["four.txt", "failed", "Only a regular file can be replaced"],
    ]);
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
      ["two.txt", "failed", "Only a regular file can be replaced"],
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
