import { render } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import FileBreadcrumb from "./FileBreadcrumb.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template, inheritAttrs: true });
  return {
    NButton: stub('<button class="stub-button" :disabled="disabled" :title="title"><slot /></button>', [
      "disabled", "title", "size", "quaternary", "loading",
    ]),
    NIcon: stub("<span><slot /></span>"),
    NSpace: stub("<div><slot /></div>"),
    useMessage: () => ({ success: vi.fn(), error: vi.fn() }),
  };
});
vi.mock("@/components/GitBadge.vue", () => ({ default: { template: "<span />" } }));
vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return Object.fromEntries(
    ["PhArrowClockwise", "PhArrowLeft", "PhClipboard", "PhHouse", "PhTerminalWindow"].map((n) => [n, stub(n)]),
  );
});

const i18nPlugin = { install(app: App) { createI18n(app); } };

const upButton = (dir: string) => {
  const { container } = render(FileBreadcrumb, {
    props: { currentDir: dir, gitInfo: null, selectedBox: "local", refreshing: false },
    global: { plugins: [i18nPlugin] },
  });
  return container.querySelector('[data-icon="PhArrowLeft"]')!.closest("button")!;
};

describe("FileBreadcrumb: Up is disabled at a root", () => {
  // Mutation: only `/` and `~` count as a root (the pre-fix test): Up stays live at `C:/`.
  it.each(["/", "~", "C:/", "//srv/share/"])("at %s", (dir) => {
    expect(upButton(dir).disabled).toBe(true);
  });
  it.each(["/home", "C:/Users", "~/x"])("not at %s", (dir) => {
    expect(upButton(dir).disabled).toBe(false);
  });
});
