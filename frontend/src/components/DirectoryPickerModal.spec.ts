import { fireEvent, render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import DirectoryPickerModal from "./DirectoryPickerModal.vue";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/api/http", () => ({ http: { get }, buildHeaders: () => ({}) }));
vi.mock("@/stores/favorites", () => ({
  useFavoritesStore: () => ({ favoritesForBox: () => new Map(), isFavorite: () => false, toggle: vi.fn() }),
}));
vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NModal: stub('<div v-if="show"><slot /></div>', ["show", "title", "preset", "bordered"]),
    NButton: stub(
      '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      ["disabled", "size", "quaternary", "type", "tertiary"],
      ["click"],
    ),
    NIcon: stub("<span><slot /></span>"),
    NSpace: stub("<div><slot /></div>"),
    NSpin: stub("<div><slot /></div>"),
    NEmpty: stub("<div />"),
    NInput: stub("<input />"),
  };
});
vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return Object.fromEntries(
    ["PhFolder", "PhFolderOpen", "PhStar", "PhArrowLeft", "PhHouse", "PhCheck"].map((n) => [n, stub(n)]),
  );
});

const i18nPlugin = { install(app: App) { createI18n(app); } };

const open = async (dir: string) => {
  get.mockResolvedValue({ data: { directory: dir, entries: [] } });
  const utils = render(DirectoryPickerModal, {
    props: { show: false, boxName: "local", token: "t", initialPath: dir },
    global: { plugins: [i18nPlugin] },
  });
  await utils.rerender({ show: true });
  await waitFor(() => expect(get).toHaveBeenCalled());
  get.mockClear();
  const up = () => utils.container.querySelector('[data-icon="PhArrowLeft"]')!.closest("button")!;
  await waitFor(() => expect(up()).toBeTruthy());
  return { up };
};

describe("DirectoryPickerModal: up on a Windows drive", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    get.mockReset();
  });

  // Mutation: cut at the last `/` (the pre-fix rule): `C:/Users` asks for `C:`.
  it.each([
    ["C:/Users/me", "C:/Users"],
    ["C:/Users", "C:/"],
    ["/home/me", "/home"],
    ["/home", "/"],
  ])("from %s it asks for %s", async (dir, parent) => {
    const { up } = await open(dir);
    await fireEvent.click(up());
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    const url = get.mock.calls[0]![0] as string;
    expect(decodeURIComponent(url.split("directory=")[1]!)).toBe(parent);
  });

  it("is disabled at C:/", async () => {
    const { up } = await open("C:/");
    expect(up().disabled).toBe(true);
  });
});
