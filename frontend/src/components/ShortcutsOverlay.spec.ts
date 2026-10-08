import { render, fireEvent } from "@testing-library/vue";
import { describe, it, expect, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import ShortcutsOverlay from "./ShortcutsOverlay.vue";
import type { NavShortcutLink } from "./ShortcutsOverlay.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NButton: stub("<button><slot /></button>"),
    NIcon: stub('<span class="stub-icon" />'),
    NList: stub("<div><slot /></div>"),
    NListItem: stub('<div class="stub-item"><slot /></div>'),
    NModal: stub('<div class="stub-modal" v-if="show"><slot name="header" /><slot /></div>', ["show"]),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhKeyboard: stub("PhKeyboard"),
    PhMagnifyingGlass: stub("PhMagnifyingGlass"),
    PhFolderSimple: stub("PhFolderSimple"),
    PhTerminal: stub("PhTerminal"),
  };
});

const i18nPlugin = {
  install(app: App) {
    createI18n(app);
  },
};

// Fixture mirroring the SHAPE AppHeader passes in (to/label/icon/shortcut) —
// this is a stand-in for whatever AppHeader.links currently contains, not a
// second copy of its content. The test only cares that the overlay renders
// exactly one row per link it was given (plus the Cmd+K row it owns itself),
// so if AppHeader ever adds/removes a link, the SAME prop is what feeds this
// component in production — the count relationship under test doesn't change.
function makeLinks(n: number): NavShortcutLink[] {
  const icon = { name: "PhFolderSimple", template: '<span data-icon="PhFolderSimple" />' };
  return Array.from({ length: n }, (_, i) => ({
    to: `/route-${i}`,
    label: `Route ${i}`,
    icon,
    shortcut: `Alt+${String.fromCharCode(97 + i)}`,
  }));
}

async function mountOverlay(links: NavShortcutLink[]) {
  const utils = render(ShortcutsOverlay, {
    props: { links },
    global: { plugins: [i18nPlugin] },
  });
  const openBtn = utils.container.querySelector("button") as HTMLElement;
  await fireEvent.click(openBtn);
  return utils;
}

describe("ShortcutsOverlay", () => {
  it("renders exactly one row per implemented shortcut: N links + the Cmd+K row it owns", async () => {
    const links = makeLinks(10);
    const { container } = await mountOverlay(links);

    const rows = container.querySelectorAll(".stub-item");
    expect(rows).toHaveLength(11);
  });

  it("would FAIL if a link were dropped — row count tracks the links prop exactly", async () => {
    const links = makeLinks(3);
    const { container } = await mountOverlay(links);

    expect(container.querySelectorAll(".stub-item")).toHaveLength(4);
  });

  it("renders the exact label and combo text for each passed-in link, plus Cmd/Ctrl+K", async () => {
    const links: NavShortcutLink[] = [
      { to: "/files", label: "Files", icon: { template: "<span />" }, shortcut: "Alt+F" },
      { to: "/terminal", label: "Terminal", icon: { template: "<span />" }, shortcut: "Alt+T" },
    ];
    const { container } = await mountOverlay(links);

    const rows = Array.from(container.querySelectorAll(".stub-item")).map((el) => el.textContent);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toContain("Command Palette");
    expect(rows[0]).toContain("Cmd/Ctrl + K");
    expect(rows[1]).toContain("Files");
    expect(rows[1]).toContain("Alt + F");
    expect(rows[2]).toContain("Terminal");
    expect(rows[2]).toContain("Alt + T");
  });
});
