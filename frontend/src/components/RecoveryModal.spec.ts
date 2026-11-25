import { fireEvent, render, waitFor } from "@testing-library/vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import type { LostSession } from "@/api/types";
import RecoveryModal from "./RecoveryModal.vue";

const { messageSpy } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/api/http", () => ({
  recreateSessionBatch: vi.fn(),
  dismissRecovery: vi.fn(),
  dismissRecoverySession: vi.fn(),
}));

import { recreateSessionBatch } from "@/api/http";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NModal: stub('<div v-if="show"><slot /><slot name="footer" /></div>', ["show"]),
    NButton: stub('<button class="stub-button" @click="$emit(\'click\')"><slot /></button>', [], ["click"]),
    NCheckbox: stub("<input type='checkbox' />"),
    NIcon: stub("<span />"),
    NSpace: stub("<div><slot /></div>"),
    NTag: stub("<span><slot /></span>"),
    useMessage: () => messageSpy,
  };
});

function lost(id: string, name: string): LostSession {
  return {
    id, box: "local", session_name: name, working_directory: "/srv", last_snapshot_at: Date.now() / 1000,
    windows: [{ index: 0, name: "w", command: "bash", path: "/srv", panes: [] }],
  };
}

const sessions = [lost("s1", "api"), lost("s2", "web")];

async function recreateAll() {
  const utils = render(RecoveryModal, { props: { show: true, sessions, token: "t" } });
  const button = Array.from(utils.container.querySelectorAll(".stub-button")).find(
    (b) => b.textContent?.trim() === "Recreate All",
  )!;
  await fireEvent.click(button);
  await waitFor(() => expect(vi.mocked(recreateSessionBatch)).toHaveBeenCalledTimes(1));
  return utils;
}

/** The texts of a session's header row, one per element or text in it. */
function rowParts(container: Element, name: string): string[] {
  const strong = Array.from(container.querySelectorAll("strong, a")).find((el) => el.textContent?.trim() === name)!;
  return Array.from(strong.parentElement!.childNodes)
    .filter((node) => node.nodeType !== Node.COMMENT_NODE)
    .map((node) => node.textContent?.trim() ?? "")
    .filter((text) => text !== "");
}

describe("RecoveryModal: recreating sessions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("every session that could not be recreated shows its name with why", async () => {
    vi.mocked(recreateSessionBatch).mockResolvedValue({
      status: "partial",
      succeeded: [],
      failed: [
        { path: "s1", error: "lb: connection refused", reason: "unreachable" },
        { path: "s2", error: "Failed to recreate tmux session", reason: "failed" },
      ],
    });
    const { container } = await recreateAll();

    await waitFor(() =>
      expect(rowParts(container, "api")).toEqual(["api", "1w", "failed", "Cannot connect to the box — lb: connection refused"]),
    );
    expect(rowParts(container, "web")).toEqual(["web", "1w", "failed", "The operation failed — Failed to recreate tmux session"]);
    expect(messageSpy.error.mock.calls).toEqual([["2 session(s) could not be recreated"]]);
    expect(messageSpy.success).toHaveBeenCalledTimes(0);
  });

  it("a row shows one state: ready, failed with its text, or the age of its snapshot", async () => {
    const older = [lost("s1", "api"), lost("s2", "web"), lost("s3", "db")].map((s) => ({
      ...s,
      last_snapshot_at: Date.now() / 1000 - 7200,
    }));
    vi.mocked(recreateSessionBatch).mockResolvedValue({
      status: "partial",
      succeeded: ["s1"],
      failed: [{ path: "s2", error: "Failed to recreate tmux session", reason: "failed" }],
    });
    const { container } = render(RecoveryModal, {
      props: { show: true, sessions: older, token: "t" },
    });
    expect(rowParts(container, "api")).toEqual(["api", "1w", "2h ago"]);
    expect(rowParts(container, "db")).toEqual(["db", "1w", "2h ago"]);

    const button = Array.from(container.querySelectorAll(".stub-button")).find(
      (b) => b.textContent?.trim() === "Recreate All",
    )!;
    await fireEvent.click(button);

    await waitFor(() => expect(rowParts(container, "web")).toEqual(["web", "1w", "failed", "The operation failed — Failed to recreate tmux session"]));
    expect(rowParts(container, "api")).toEqual(["api", "1w", "ready"]);
  });

  it("a failed request shows the failure's message", async () => {
    vi.mocked(recreateSessionBatch).mockRejectedValue(new ApiError(0, "network", ""));
    await recreateAll();

    await waitFor(() =>
      expect(messageSpy.error.mock.calls).toEqual([["No answer from sshler; check the connection"]]),
    );
  });
});
