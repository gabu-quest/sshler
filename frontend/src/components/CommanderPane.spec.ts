import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import CommanderPane from "./CommanderPane.vue";

vi.mock("@/api/http", () => ({
  fetchDirectory: vi.fn(async (_box: string, directory: string) => ({ box: "local", directory, entries: [] })),
  gitInfo: vi.fn(async () => null),
}));
vi.mock("naive-ui", () => ({
  NSelect: { template: "<select />" },
  NIcon: { template: "<span><slot /></span>" },
}));
vi.mock("@phosphor-icons/vue", () => {
  const stub = { template: "<span />" };
  return { PhArrowBendUpLeft: stub, PhFile: stub, PhFolderSimple: stub };
});

Element.prototype.scrollIntoView = vi.fn();

const i18nPlugin = { install(app: App) { createI18n(app); } };

async function mountAt(directory: string) {
  const wrapper = mount(CommanderPane, {
    props: {
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
