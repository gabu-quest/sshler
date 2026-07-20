import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import type { ClaudeSession } from "@/api/types";
import { createI18n } from "@/i18n";
import ClaudeSessionsView from "./ClaudeSessionsView.vue";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/api/http", () => ({
  fetchClaudeSessions: vi.fn(),
  openClaudeSession: vi.fn(),
}));

import { fetchClaudeSessions, openClaudeSession } from "@/api/http";

const mockFetch = vi.mocked(fetchClaudeSessions);
const mockOpen = vi.mocked(openClaudeSession);

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NCard: stub('<div class="stub-card"><slot /></div>'),
    NEmpty: stub('<div class="stub-empty">{{ description }}<slot name="extra" /></div>', [
      "description",
    ]),
    NButton: stub(
      '<button class="stub-button" :data-loading="loading" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
      ["loading", "type"],
    ),
    NInput: stub(
      '<input class="stub-input" :value="value" @input="$emit(\'update:value\', $event.target.value)" />',
      ["value", "placeholder"],
    ),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NTag: stub('<span class="stub-tag"><slot name="icon" /><slot /></span>'),
    NSpin: stub('<div class="stub-spin" />'),
    // Collapse/timeline stubs always render their slots (expansion isn't tested).
    NCollapse: stub('<div class="stub-collapse"><slot /></div>', ["expandedNames"]),
    NCollapseItem: stub(
      '<div class="stub-collapse-item"><slot name="header" /><slot name="header-extra" /><slot /></div>',
      ["name"],
    ),
    NTimeline: stub('<div class="stub-timeline"><slot /></div>'),
    NTimelineItem: stub('<div class="stub-timeline-item"><slot name="icon" /><slot /></div>', ["type"]),
    // Render only the trigger (faithful: content is hidden until opened).
    NPopover: stub('<div class="stub-popover"><slot name="trigger" /></div>'),
    // Render content only when shown, like the real modal.
    NModal: stub('<div class="stub-modal"><slot /></div>', ["show", "title", "preset"]),
    useMessage: () => ({
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      warning: vi.fn(),
    }),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhArrowCounterClockwise: stub("PhArrowCounterClockwise"),
    PhArrowLineUpRight: stub("PhArrowLineUpRight"),
    PhArrowsClockwise: stub("PhArrowsClockwise"),
    PhArrowsIn: stub("PhArrowsIn"),
    PhArrowsOut: stub("PhArrowsOut"),
    PhCopy: stub("PhCopy"),
    PhFileText: stub("PhFileText"),
    PhFolder: stub("PhFolder"),
    PhGitBranch: stub("PhGitBranch"),
    PhPlay: stub("PhPlay"),
    PhRobot: stub("PhRobot"),
    PhSlidersHorizontal: stub("PhSlidersHorizontal"),
    PhTag: stub("PhTag"),
  };
});

const i18nPlugin = {
  install(app: App) {
    createI18n(app);
  },
};

function makeSession(id: string, over: Partial<ClaudeSession> = {}): ClaudeSession {
  return {
    id,
    cwd: "/proj",
    title: `Title ${id}`,
    last_prompt: null,
    last_active: Date.now() / 1000 - 30,
    git_branch: null,
    version: null,
    size_bytes: 100,
    project_dir: "-proj",
    repo_root: null,
    ...over,
  };
}

function mountView() {
  return render(ClaudeSessionsView, { global: { plugins: [i18nPlugin] } });
}

describe("ClaudeSessionsView", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    mockFetch.mockReset();
    mockOpen.mockReset();
    pushMock.mockReset();
  });

  it("renders a row per session with its title", async () => {
    mockFetch.mockResolvedValue([
      makeSession("a", { title: "Alpha session" }),
      makeSession("b", { title: "Beta session" }),
    ]);

    const screen = mountView();
    const rows = await screen.findAllByTestId("claude-session");
    expect(rows).toHaveLength(2);

    const titles = screen.getAllByTestId("claude-title").map((el) => el.textContent?.trim());
    expect(titles).toEqual(["Alpha session", "Beta session"]);
  });

  it("shows relative last-active time", async () => {
    mockFetch.mockResolvedValue([
      makeSession("a", { title: "Alpha", last_active: Date.now() / 1000 - 30 }),
    ]);
    const screen = mountView();
    await screen.findAllByTestId("claude-session");
    expect(screen.getByText(/active 30s ago/)).toBeTruthy();
  });

  it("filters sessions by query", async () => {
    mockFetch.mockResolvedValue([
      makeSession("a", { title: "Alpha session" }),
      makeSession("b", { title: "Beta session" }),
    ]);
    const screen = mountView();
    await screen.findAllByTestId("claude-session");

    const input = screen.container.querySelector(".stub-input") as HTMLInputElement;
    await fireEvent.update(input, "Beta");

    const rows = screen.getAllByTestId("claude-session");
    expect(rows).toHaveLength(1);
    expect(screen.getAllByTestId("claude-title")[0].textContent?.trim()).toBe("Beta session");
  });

  it("primary resume opens in the background and stays on the list", async () => {
    mockFetch.mockResolvedValue([makeSession("a", { title: "Alpha" })]);
    mockOpen.mockResolvedValue({
      box: "local",
      session_name: "proj",
      working_directory: "/proj",
      window: "cl-a1b2c3",
      already_open: false,
    });

    const screen = mountView();
    await screen.findAllByTestId("claude-session");

    await fireEvent.click(screen.getByTestId("resume-a"));
    await vi.waitFor(() => expect(mockOpen).toHaveBeenCalled());

    // Resume sends the resolved command template (default here)…
    expect(mockOpen).toHaveBeenCalledWith("a", null, "claude --resume {id}");
    // …and does NOT navigate away — the user stays in the list.
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("the open button resumes AND routes to the terminal", async () => {
    mockFetch.mockResolvedValue([makeSession("a", { title: "Alpha" })]);
    mockOpen.mockResolvedValue({
      box: "local",
      session_name: "proj",
      working_directory: "/proj",
      window: "cl-a1b2c3",
      already_open: false,
    });

    const screen = mountView();
    await screen.findAllByTestId("claude-session");

    await fireEvent.click(screen.getByTestId("resume-open-a"));
    await vi.waitFor(() => expect(pushMock).toHaveBeenCalled());

    expect(mockOpen).toHaveBeenCalledWith("a", null, "claude --resume {id}");
    expect(pushMock).toHaveBeenCalledWith({
      name: "terminal",
      query: { box: "local", dir: "/proj", session: "proj" },
    });
  });

  it("groups sessions by git repo root and labels the group by repo name", async () => {
    // Two sessions in the same repo but different subdirs (mirrors my-project
    // + my-project/worker) must collapse into ONE group, matching /resume.
    mockFetch.mockResolvedValue([
      makeSession("a", {
        title: "at root",
        cwd: "/home/user/projects/my-project",
        repo_root: "/home/user/projects/my-project",
      }),
      makeSession("b", {
        title: "in subdir",
        cwd: "/home/user/projects/my-project/worker",
        repo_root: "/home/user/projects/my-project",
      }),
    ]);

    const screen = mountView();
    await screen.findAllByTestId("claude-session");

    const groupLabels = screen.getAllByTestId("claude-group").map((el) => el.textContent?.trim());
    expect(groupLabels).toEqual(["my-project"]);

    // Both sessions are inside that one group…
    expect(screen.getAllByTestId("claude-session")).toHaveLength(2);
    // …and the subdir session is tagged with its path relative to the repo root.
    expect(screen.getByText("worker")).toBeTruthy();
  });

  it("splits sessions from different repos into separate groups", async () => {
    mockFetch.mockResolvedValue([
      makeSession("a", { title: "one", cwd: "/work/alpha", repo_root: "/work/alpha" }),
      makeSession("b", { title: "two", cwd: "/work/beta", repo_root: "/work/beta" }),
    ]);

    const screen = mountView();
    await screen.findAllByTestId("claude-session");

    const groupLabels = screen.getAllByTestId("claude-group").map((el) => el.textContent?.trim());
    expect(groupLabels).toEqual(["alpha", "beta"]);
  });

  it("a per-session override is saved and sent on resume", async () => {
    localStorage.clear();
    mockFetch.mockResolvedValue([makeSession("a", { title: "Alpha" })]);
    mockOpen.mockResolvedValue({
      box: "local",
      session_name: "x",
      working_directory: "/proj",
      window: "cl-abc123",
      already_open: false,
    });

    const screen = mountView();
    await screen.findAllByTestId("claude-session");

    // Open the per-row command editor, set an override, save.
    await fireEvent.click(screen.getByTestId("cmd-a"));
    await fireEvent.update(
      screen.getByTestId("claude-override-input") as HTMLInputElement,
      "claudeee --resume {id}",
    );
    await fireEvent.click(screen.getByTestId("claude-override-save"));

    // Resume now sends the override, not the global default.
    await fireEvent.click(screen.getByTestId("resume-a"));
    await vi.waitFor(() => expect(mockOpen).toHaveBeenCalled());
    expect(mockOpen).toHaveBeenCalledWith("a", null, "claudeee --resume {id}");
  });
});
