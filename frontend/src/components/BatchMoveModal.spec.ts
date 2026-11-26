import { render } from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import BatchMoveModal from "./BatchMoveModal.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NModal: stub('<div v-if="show"><slot /></div>', ["show"]),
    NCard: stub('<div class="card"><h2>{{ title }}</h2><slot /><div class="actions"><slot name="action" /></div></div>', ["title"]),
    NButton: stub('<button><slot /></button>', ["type", "disabled"]),
    NSpace: stub("<div><slot /></div>"),
    NIcon: stub("<span><slot /></span>"),
  };
});
vi.mock("@/i18n", async () => await vi.importActual("@/i18n"));
vi.mock("@phosphor-icons/vue", () => ({
  PhArrowBendUpLeft: { template: "<i />" },
  PhCopy: { template: "<i />" },
}));
vi.mock("@/components/DirectorySearchInput.vue", () => ({ default: { template: "<div />" } }));

let lang: "en" | "ja" = "en";
const i18nPlugin = { install(app: App) { createI18n(app).setLocale(lang); } };
const mount = (mode: "move" | "copy") =>
  render(BatchMoveModal, {
    props: { visible: true, mode, box: "local", token: null, count: 3 },
    global: { plugins: [i18nPlugin] },
  });
const words = (c: Element) => (c.textContent ?? "").replace(/\s+/g, " ");

describe("BatchMoveModal: every text is a key", () => {
  afterEach(() => { lang = "en"; });

  it("in English", () => {
    const { container } = mount("move");
    expect(words(container)).toContain("Move 3 item(s)");
    expect(words(container)).toContain("Search for a destination directory:");
    expect(words(container)).toContain("Move here");
    expect(words(container)).toContain("Cancel");
  });

  it("in Japanese, move and copy", () => {
    lang = "ja";
    const move = mount("move").container;
    expect(words(move)).toContain("3 件を移動");
    expect(words(move)).toContain("移動先のディレクトリを検索:");
    expect(words(move)).toContain("ここに移動");
    expect(words(move)).not.toContain("Cancel");
    const copy = mount("copy").container;
    expect(words(copy)).toContain("3 件をコピー");
    expect(words(copy)).toContain("ここにコピー");
  });
});
