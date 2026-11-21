import { render, waitFor } from "@testing-library/vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import CommanderGitLog from "./CommanderGitLog.vue";

vi.mock("@/api/http", () => ({
  gitLog: vi.fn(),
  gitBranches: vi.fn(),
}));

import { gitBranches, gitLog } from "@/api/http";

// Both panes render at once, so the commits and the branches can be read together.
vi.mock("naive-ui", () => ({
  NSpin: { template: "<span data-testid='spinner' />" },
  NTabs: { template: "<div><slot /></div>", props: ["value", "type", "size", "animated"] },
  NTabPane: { template: "<section :data-testid=\"'pane-' + name\"><slot /></section>", props: ["name", "tab"] },
}));

const props = { show: true, box: "build-box", directory: "/srv/app", token: "t" };

function emptyText(pane: HTMLElement): string[] {
  return Array.from(pane.querySelectorAll(".git-log-empty")).map((el) => el.textContent?.trim() ?? "");
}

describe("CommanderGitLog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("a box that cannot be reached shows the failure's message, not an empty repository", async () => {
    const failure = new ApiError(502, "unreachable", "build-box did not answer");
    vi.mocked(gitLog).mockRejectedValue(failure);
    vi.mocked(gitBranches).mockRejectedValue(failure);

    const { getByTestId } = render(CommanderGitLog, { props });

    await waitFor(() => expect(getByTestId("pane-commits").querySelector(".git-log-empty")).not.toBeNull());
    expect(emptyText(getByTestId("pane-commits"))).toEqual(["Cannot connect to the box — build-box did not answer"]);
    expect(emptyText(getByTestId("pane-branches"))).toEqual(["Cannot connect to the box — build-box did not answer"]);
  });

  it("a folder that is not a repository shows no commits and no branches", async () => {
    vi.mocked(gitLog).mockResolvedValue({ box: "build-box", directory: "/srv/app", commits: [], is_repo: false });
    vi.mocked(gitBranches).mockResolvedValue({
      box: "build-box", directory: "/srv/app", branches: [], root: null, is_repo: false,
    });

    const { getByTestId } = render(CommanderGitLog, { props });

    await waitFor(() => expect(getByTestId("pane-commits").querySelector(".git-log-empty")).not.toBeNull());
    expect(emptyText(getByTestId("pane-commits"))).toEqual(["No commits found"]);
    expect(emptyText(getByTestId("pane-branches"))).toEqual(["No branches"]);
  });
});
