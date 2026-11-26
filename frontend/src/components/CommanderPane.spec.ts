import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import * as http from "@/api/http";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import CommanderPane from "./CommanderPane.vue";

vi.mock("@/api/http", () => ({
  fetchDirectory: vi.fn(async (_box: string, directory: string) => ({ box: "local", directory, entries: [] })),
  gitInfo: vi.fn(async (): Promise<unknown> => null),
}));
vi.mock("naive-ui", () => ({
  NSelect: { template: "<select />" },
  NIcon: { template: "<span><slot /></span>" },
}));
vi.mock("@phosphor-icons/vue", () => {
  const stub = { template: "<span />" };
  return { PhArrowBendUpLeft: stub, PhFile: stub, PhFolderSimple: stub, PhGitBranch: stub };
});

// the real i18n, so the Japanese words can be read
vi.mock("@/i18n", async () => await vi.importActual("@/i18n"));

Element.prototype.scrollIntoView = vi.fn();

let i18n: ReturnType<typeof createI18n>;
const i18nPlugin = { install(app: App) { i18n = createI18n(app); } };

async function mountAt(directory: string, extra: Record<string, unknown> = {}) {
  const wrapper = mount(CommanderPane, {
    props: {
      ...extra,
      box: "local", directory, selected: [], active: true, token: "tok",
      boxOptions: [{ name: "local", host: "local" }],
    },
    global: { plugins: [i18nPlugin] },
  });
  await flushPromises();
  return wrapper;
}

const asked = (wrapper: Awaited<ReturnType<typeof mountAt>>) =>
  (wrapper.emitted("update:directory") ?? []).map((args) => args[0]);

describe("CommanderPane parent and breadcrumbs", () => {
  // Mutation: put back the old getParentDirectory (cut at the last `/`, `|| "/"`): from
  // `C:/Users` it answers `C:`; from `//srv/share/d` it answers `//srv/share`.
  it.each([
    ["C:/Users", "C:/"],
    ["C:/Users/me", "C:/Users"],
    ["/home/me", "/home"],
    ["/home", "/"],
    ["~/a", "~"],
    ["//srv/share/d", "//srv/share/"],
  ])("goUp from %s asks for %s", async (directory, parent) => {
    const wrapper = await mountAt(directory);
    (wrapper.vm as unknown as { goUp: () => void }).goUp();
    expect(asked(wrapper)).toEqual([parent]);
  });

  it.each(["/", "~", "C:/"])("goUp at the root %s stays on it", async (directory) => {
    const wrapper = await mountAt(directory);
    (wrapper.vm as unknown as { goUp: () => void }).goUp();
    expect(asked(wrapper)).toEqual([directory]);
  });

  it("the `..` row of a drive folder opens the drive root", async () => {
    const wrapper = await mountAt("C:/Users");
    await wrapper.find(".file-row").trigger("dblclick");
    expect(asked(wrapper)).toEqual(["C:/"]);
  });

  // Mutation: put back the old buildBreadcrumbs (every path starts at `/`): a drive path
  // gives crumbs `/`, `/C:`, `/C:/Users`.
  it("a drive path has crumbs from the drive root, each opening its own folder", async () => {
    const wrapper = await mountAt("C:/Users/me");
    const crumbs = wrapper.findAll(".crumb");
    expect(crumbs.map((c) => c.find(".crumb-text").text())).toEqual(["C:/", "Users", "me"]);
    await crumbs[0]!.trigger("click");
    await crumbs[1]!.trigger("click");
    expect(asked(wrapper)).toEqual(["C:/", "C:/Users"]);
  });

  it("a share path has crumbs from the share", async () => {
    const wrapper = await mountAt("//srv/sh/d");
    expect(wrapper.findAll(".crumb-text").map((c) => c.text())).toEqual(["//srv/sh", "d"]);
  });
});

describe("CommanderPane texts, git badge and colours", () => {
  const repo = { is_repo: true, branch: "main", commit: "abc1234", dirty: false };

  // Mutation: the column heads, tooltips and footer are English literals.
  it("the column heads, the footer and the tooltips read through the texts, in Japanese too", async () => {
    try {
      vi.mocked(http.fetchDirectory).mockResolvedValueOnce({
        box: "local", directory: "/d",
        entries: [{ name: "a.txt", path: "/d/a.txt", is_directory: false, size: 1, modified: 0, mode: 0o644 }],
      } as never);
      const wrapper = await mountAt("/d", { matchedFiles: new Set(["a.txt"]), branchChangedFiles: new Set(["a.txt"]) });
      i18n.setLocale("ja");
      await wrapper.vm.$nextTick();
      expect(wrapper.findAll(".column-header span").map((c) => c.text())).toEqual(["名前", "サイズ", "更新日時", "権限"]);
      expect(wrapper.find(".pane-footer").text()).toContain("合計");
      expect(wrapper.find(".match-indicator").attributes("title")).toBe("もう一方のペインに同名ファイルあり");
      expect(wrapper.find(".branch-changed-indicator").attributes("title")).toBe("ブランチ間で変更あり");
    } finally {
      i18n?.setLocale("en");
    }
  });

  it("shows the badge of a repository and nothing when the git load fails", async () => {
    vi.mocked(http.gitInfo).mockResolvedValueOnce(repo as never);
    const wrapper = await mountAt("/repo");
    expect(wrapper.find(".git-badge").text()).toContain("main");
    vi.mocked(http.gitInfo).mockRejectedValueOnce(new Error("boom"));
    const failed = await mountAt("/other");
    expect(failed.find(".git-badge").exists()).toBe(false);
    expect(failed.find(".error-state").exists()).toBe(false);
  });

  // Mutation: a folder left before its git answer came shows that answer's badge.
  it("drops a git answer for a folder the pane has since left", async () => {
    let late!: (value: never) => void;
    vi.mocked(http.gitInfo).mockImplementationOnce(() => new Promise((resolve) => { late = resolve; }));
    const wrapper = await mountAt("/old");
    await wrapper.setProps({ directory: "/new" });
    await flushPromises();
    late(repo as never);
    await flushPromises();
    expect(wrapper.find(".git-badge").exists()).toBe(false);
  });

  // Mutation: a hard-coded dark colour comes back and the light theme is unreadable.
  it("the style uses the theme tokens only", async () => {
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(`${process.cwd()}/src/components/CommanderPane.vue`, "utf8");
    const css = source.slice(source.indexOf("<style"));
    expect(css.match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull();
  });
});
