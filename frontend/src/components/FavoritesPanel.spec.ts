import { fireEvent, render } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import { useFavoritesStore } from "@/stores/favorites";
import FavoritesPanel from "./FavoritesPanel.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub(
      '<button class="stub-button" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      ["disabled", "size", "secondary", "quaternary"],
      ["click"],
    ),
    NCard: stub('<div class="stub-card"><slot /></div>', ["size"]),
    NIcon: stub("<span><slot /></span>"),
    NList: stub('<ul><slot /></ul>'),
    NListItem: stub('<li class="fav-item" @click="$emit(\'click\')"><slot /></li>', [], ["click"]),
  };
});
vi.mock("@phosphor-icons/vue", () => ({
  PhStar: { template: "<span />" },
  PhPushPinSimple: { template: "<span />" },
}));

const i18nPlugin = { install(app: App) { createI18n(app); } };

const mount = (props: { box: string | null; compact?: boolean }) =>
  render(FavoritesPanel, { props, global: { plugins: [i18nPlugin] } });

describe("FavoritesPanel compact mode", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useFavoritesStore().hydrateFromBoxes([
      { name: "my box", host: "h", favorites: ["/srv/a b%20", "~/proj", "C:/Users"], pinned: false } as never,
    ]);
  });

  it("is a strip of chips, not a card", () => {
    const { container } = mount({ box: "my box", compact: true });
    expect(container.querySelector(".fav-strip")).not.toBeNull();
    expect(container.querySelector(".stub-card")).toBeNull();
    expect(Array.from(container.querySelectorAll(".fav-chip")).map((c) => c.textContent?.trim())).toEqual([
      "a b%20", "proj", "Users",
    ]);
  });

  it("a chip opens its full path, spaces and percent signs untouched", async () => {
    const { container, emitted } = mount({ box: "my box", compact: true });
    await fireEvent.click(container.querySelectorAll(".fav-chip")[0]!);
    expect(emitted().openPath).toEqual([["/srv/a b%20"]]);
  });

  it("without a box it shows the hint and no chips", () => {
    const { container } = mount({ box: null, compact: true });
    expect(container.querySelector(".fav-strip")).not.toBeNull();
    expect(container.querySelectorAll(".fav-chip")).toHaveLength(0);
    expect(container.textContent).toContain("select a box to view favorites");
  });

  it("the pin button still toggles the pin", async () => {
    const { container, emitted } = mount({ box: "my box", compact: true });
    await fireEvent.click(container.querySelector(".fav-pin")!);
    expect(emitted().togglePin).toHaveLength(1);
  });

  it("two chips with the same last folder show the parent too; a unique one stays short", () => {
    setActivePinia(createPinia());
    useFavoritesStore().hydrateFromBoxes([
      { name: "b2", host: "h", favorites: ["/srv/api/src", "/srv/web/src", "/srv/docs"], pinned: false } as never,
    ]);
    const { container } = mount({ box: "b2", compact: true });
    expect(Array.from(container.querySelectorAll(".fav-chip")).map((c) => c.textContent?.trim())).toEqual([
      "api/src", "web/src", "docs",
    ]);
  });
});
