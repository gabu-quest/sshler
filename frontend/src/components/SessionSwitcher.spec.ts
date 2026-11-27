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
  recreateSession: vi.fn(),
}));

import { deleteSession, fetchBoxSessions, recreateSession, renameSession, syncBoxSessions } from "@/api/http";

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
  created_at: 0, last_accessed_at: 0, active: true, window_count: 1, metadata: {}, restorable: false,
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
    vi.mocked(syncBoxSessions).mockRejectedValue(failure);
    vi.mocked(fetchBoxSessions).mockRejectedValue(new ApiError(502, "unreachable", "stored list failed"));
    const { getByTestId } = mountSwitcher();
    await waitFor(() => expect(getByTestId("empty").textContent).toBe(failureLine));
  });

  it("a failed load still lists the stored sessions, with the failure's line", async () => {
    vi.mocked(syncBoxSessions).mockRejectedValue(failure);
    vi.mocked(fetchBoxSessions).mockResolvedValue([session, { ...session, id: "s2", session_name: "web" }]);
    const { container, findByText, getByTestId } = mountSwitcher();
    await findByText("web");

    const names = [...container.querySelectorAll(".session-list .session-item .session-name span:last-child")];
    expect(names.map((name) => name.textContent)).toEqual(["api", "web"]);
    expect(getByTestId("load-error").textContent).toBe(failureLine);
    expect(vi.mocked(fetchBoxSessions).mock.calls).toEqual([["build-box", "t"]]);
  });

  it("a failed sync shows its message and keeps the sessions listed", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValueOnce([session]).mockRejectedValueOnce(failure);
    const { container, findByText } = mountSwitcher();
    await findByText("api");
    await fireEvent.click(container.querySelector(".session-header .stub-button")!);

    await waitFor(() => expect(messageSpy.error.mock.calls).toEqual([[failureLine]]));
    expect(container.querySelectorAll(".session-list .session-item")).toHaveLength(1);
  });

  it("a failed kill shows its message and keeps the session listed", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValue([session]);
    vi.mocked(deleteSession).mockRejectedValue(failure);
    const { container, findByText } = mountSwitcher();
    await findByText("api");
    await fireEvent.click(container.querySelector(".session-list .confirm-yes")!);

    await waitFor(() => expect(messageSpy.error.mock.calls).toEqual([[failureLine]]));
    expect(container.querySelectorAll(".session-list .session-item")).toHaveLength(1);
  });

  it("a failed rename shows its message and keeps the new name in its field", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValue([session]);
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

describe("SessionSwitcher: what it loads", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads by a sync, so a session tmux no longer has shows as not running", async () => {
    // The stored row still says active; the sync, which asks tmux, says it ended.
    vi.mocked(fetchBoxSessions).mockResolvedValue([session]);
    vi.mocked(syncBoxSessions).mockResolvedValue([{ ...session, active: false }]);
    const { container, findByText } = mountSwitcher();
    await findByText("api");

    const items = container.querySelectorAll(".session-list .session-item");
    expect(items).toHaveLength(1);
    expect(items[0].classList.contains("inactive")).toBe(true);
    expect(vi.mocked(syncBoxSessions).mock.calls).toEqual([["build-box", "t"]]);
  });
});

describe("SessionSwitcher: Restore layout", () => {
  const ended: ApiSession = { ...session, id: "s2", session_name: "web", active: false, restorable: true };
  const bare: ApiSession = { ...session, id: "s3", session_name: "old", active: false, restorable: false };

  function restoreButtons(container: Element): string[] {
    return [...container.querySelectorAll(".session-list .session-item")]
      .filter((item) => item.querySelector('[title="Restore layout"]'))
      .map((item) => item.querySelector(".session-name span:last-child")!.textContent ?? "");
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("only an ended row whose layout is kept offers Restore layout", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValue([session, ended, bare]);
    const { container, findByText } = mountSwitcher();
    await findByText("web");

    expect(restoreButtons(container)).toEqual(["web"]);
  });

  it("one press recreates the session by the recovery route, then lists it running", async () => {
    vi.mocked(syncBoxSessions)
      .mockResolvedValueOnce([ended])
      .mockResolvedValueOnce([{ ...ended, active: true, restorable: false }]);
    vi.mocked(recreateSession).mockResolvedValue({ status: "ok", message: "Recreated web" } as never);
    const { container, findByText } = mountSwitcher();
    await findByText("web");
    await fireEvent.click(container.querySelector('.session-list [title="Restore layout"]')!);

    await waitFor(() => expect(restoreButtons(container)).toEqual([]));
    expect(vi.mocked(recreateSession).mock.calls).toEqual([["s2", "t"]]);
    expect(container.querySelector(".session-list .session-item")!.classList.contains("inactive")).toBe(false);
  });

  it("a failed restore shows its message and keeps the entry", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValue([ended]);
    vi.mocked(recreateSession).mockRejectedValue(failure);
    const { container, findByText } = mountSwitcher();
    await findByText("web");
    await fireEvent.click(container.querySelector('.session-list [title="Restore layout"]')!);

    await waitFor(() => expect(messageSpy.error.mock.calls).toEqual([[failureLine]]));
    expect(restoreButtons(container)).toEqual(["web"]);
  });
});

describe("SessionSwitcher: motion", () => {
  it("the session rows are a list-row group", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValue([session]);
    const { container, findByText } = mountSwitcher();
    await findByText("api");
    const group = container.querySelector(".session-list")!;
    expect(group.tagName.toLowerCase()).toBe("transition-group-stub");
    expect(group.getAttribute("name")).toBe("list-row");
  });
});
