import { fireEvent, render, waitFor } from "@testing-library/vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import type { ApiSession } from "@/api/types";
import SessionSwitcher from "./SessionSwitcher.vue";

const { messageSpy } = vi.hoisted(() => ({ messageSpy: { error: vi.fn(), success: vi.fn() } }));

vi.mock("@/api/http", () => ({
  fetchBoxSessions: vi.fn(),
  syncBoxSessions: vi.fn(),
  deleteSession: vi.fn(),
  renameSession: vi.fn(),
}));

import { deleteSession, fetchBoxSessions, renameSession, syncBoxSessions } from "@/api/http";

// Each confirm renders its trigger and a "yes" button that confirms at once.
vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NButton: stub('<button class="stub-button" :title="title" @click="$emit(\'click\', $event)"><slot /></button>', ["title"], ["click"]),
    NIcon: stub("<span />"),
    NSpin: stub('<span class="stub-spin" />'),
    NTooltip: stub("<span><slot name='trigger' /></span>"),
    NPopconfirm: stub(
      '<span><slot name="trigger" /><button class="confirm-yes" @click="$emit(\'positive-click\', $event)">yes</button></span>',
      [],
      ["positive-click"],
    ),
    NEmpty: stub('<div data-testid="empty">{{ description }}</div>', ["description"]),
    useMessage: () => messageSpy,
  };
});

const session: ApiSession = {
  id: "s1", box: "build-box", session_name: "api", working_directory: "/srv/work",
  created_at: 0, last_accessed_at: 0, active: true, window_count: 1, metadata: {},
};

const failure = new ApiError(502, "unreachable", "build-box did not answer");
const failureLine = "Cannot connect to the box — build-box did not answer";

function mountSwitcher() {
  return render(SessionSwitcher, { props: { boxName: "build-box", token: "t" } });
}

describe("SessionSwitcher: a failed request", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("a failed load shows its message where 'No sessions' would show", async () => {
    vi.mocked(fetchBoxSessions).mockRejectedValue(failure);
    const { getByTestId } = mountSwitcher();
    await waitFor(() => expect(getByTestId("empty").textContent).toBe(failureLine));
  });

  it("a failed sync shows its message and keeps the sessions listed", async () => {
    vi.mocked(fetchBoxSessions).mockResolvedValue([session]);
    vi.mocked(syncBoxSessions).mockRejectedValue(failure);
    const { container, findByText } = mountSwitcher();
    await findByText("api");
    await fireEvent.click(container.querySelector(".session-header .stub-button")!);

    await waitFor(() => expect(messageSpy.error.mock.calls).toEqual([[failureLine]]));
    expect(container.querySelectorAll(".session-list .session-item")).toHaveLength(1);
  });

  it("a failed kill shows its message and keeps the session listed", async () => {
    vi.mocked(fetchBoxSessions).mockResolvedValue([session]);
    vi.mocked(deleteSession).mockRejectedValue(failure);
    const { container, findByText } = mountSwitcher();
    await findByText("api");
    await fireEvent.click(container.querySelector(".session-list .confirm-yes")!);

    await waitFor(() => expect(messageSpy.error.mock.calls).toEqual([[failureLine]]));
    expect(container.querySelectorAll(".session-list .session-item")).toHaveLength(1);
  });

  it("a failed rename shows its message and keeps the new name in its field", async () => {
    vi.mocked(fetchBoxSessions).mockResolvedValue([session]);
    vi.mocked(renameSession).mockRejectedValue(failure);
    const { container, findByText } = mountSwitcher();
    await findByText("api");
    await fireEvent.click(container.querySelector('.session-list [title="Rename session"]')!);
    const input = container.querySelector<HTMLInputElement>(".rename-input")!;
    await fireEvent.update(input, "api-2");
    await fireEvent.keyUp(input, { key: "Enter" });

    await waitFor(() => expect(messageSpy.error.mock.calls).toEqual([[failureLine]]));
    expect(container.querySelector<HTMLInputElement>(".rename-input")!.value).toBe("api-2");
  });
});
