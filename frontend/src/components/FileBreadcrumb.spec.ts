import { fireEvent, render } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import FileBreadcrumb from "./FileBreadcrumb.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({
    props, emits, template, inheritAttrs: true,
  });
  return {
    NButton: stub(
      '<button class="stub-button" :disabled="disabled" :title="title" @click="$emit(\'click\')"><slot /></button>',
      ["disabled", "title", "size", "quaternary", "loading"],
      ["click"],
    ),
    NIcon: stub("<span><slot /></span>"),
    NInput: stub(
      '<input class="stub-input" :value="value" @input="$emit(\'update:value\', $event.target.value)" @keyup.enter="$emit(\'keyup\', $event)" />',
      ["value", "size", "placeholder"],
      ["update:value", "keyup"],
    ),
    NSelect: stub(
      `<select class="stub-select" :disabled="disabled" @change="$emit('update:value', $event.target.value)">
        <option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>`,
      ["value", "options", "disabled", "size", "placeholder"],
      ["update:value"],
    ),
    useMessage: () => ({ success: vi.fn(), error: vi.fn() }),
  };
});
vi.mock("@/components/GitBadge.vue", () => ({ default: { template: '<span class="stub-git" />' } }));
vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return Object.fromEntries(
    ["PhArrowClockwise", "PhArrowLeft", "PhClipboard", "PhHouse", "PhPencilSimple", "PhStar", "PhTerminalWindow"].map(
      (n) => [n, stub(n)],
    ),
  );
});

const i18nPlugin = { install(app: App) { createI18n(app); } };

const mount = (props: Record<string, unknown> = {}) =>
  render(FileBreadcrumb, {
    props: { currentDir: "/", gitInfo: null, selectedBox: "local", refreshing: false, ...props },
    global: { plugins: [i18nPlugin] },
  });

const upButton = (dir: string) =>
  mount({ currentDir: dir }).container.querySelector('[data-icon="PhArrowLeft"]')!.closest("button")!;

describe("FileBreadcrumb: Up is disabled at a root", () => {
  // Mutation: only `/` and `~` count as a root (the pre-fix test): Up stays live at `C:/`.
  it.each(["/", "~", "C:/", "//srv/share/"])("at %s", (dir) => {
    expect(upButton(dir).disabled).toBe(true);
  });
  it.each(["/home", "C:/Users", "~/x"])("not at %s", (dir) => {
    expect(upButton(dir).disabled).toBe(false);
  });
});

describe("FileBreadcrumb: path segments jump", () => {
  const crumbs = (container: Element) => Array.from(container.querySelectorAll<HTMLButtonElement>(".crumb"));

  it("a click on a segment emits the path it opens", async () => {
    const { container, emitted } = mount({ currentDir: "/home/me/proj" });
    expect(crumbs(container).map((c) => c.textContent)).toEqual(["/", "home", "me", "proj"]);
    await fireEvent.click(crumbs(container)[2]!);
    expect(emitted().navigate).toEqual([["/home/me"]]);
  });

  // A name with a space and a percent sign must reach the parent untouched, not encoded.
  it("keeps a space and a percent sign in the path", async () => {
    const { container, emitted } = mount({ currentDir: "/tmp/a b%20/deep" });
    await fireEvent.click(crumbs(container)[2]!);
    expect(emitted().navigate).toEqual([["/tmp/a b%20"]]);
  });

  it("a drive keeps its root: C:/ is a crumb and opens C:/", async () => {
    const { container, emitted } = mount({ currentDir: "C:/Users/me" });
    await fireEvent.click(crumbs(container)[0]!);
    expect(emitted().navigate).toEqual([["C:/"]]);
  });

  it("a drive path draws no slash after C:/ and a POSIX path none after /", () => {
    const text = (dir: string) =>
      (mount({ currentDir: dir }).container.querySelector(".crumbs")!.textContent ?? "").trim();
    expect(text("C:/Users/me")).toBe("C:/Users/me");
    expect(text("/home/me")).toBe("/home/me");
  });

  it("the path edit sends what was typed", async () => {
    const { container, emitted } = mount({ currentDir: "/home/me" });
    await fireEvent.click(container.querySelector('[data-icon="PhPencilSimple"]')!.closest("button")!);
    const input = container.querySelector<HTMLInputElement>(".stub-input")!;
    expect(input.value).toBe("/home/me");
    await fireEvent.update(input, "/srv/my dir");
    await fireEvent.keyUp(input, { key: "Enter" });
    expect(emitted().navigate).toEqual([["/srv/my dir"]]);
  });
});

describe("FileBreadcrumb: the rest of the bar", () => {
  it("the box select reports a choice", async () => {
    const { container, emitted } = mount({
      boxOptions: [{ label: "a", value: "my box" }, { label: "b", value: "other" }],
    });
    await fireEvent.update(container.querySelector(".stub-select")!, "my box");
    expect(emitted()["update:box"]).toEqual([["my box"]]);
  });

  it("the status dot says online, offline with the reason, or nothing", () => {
    const dot = (props: Record<string, unknown>) =>
      mount(props).container.querySelector<HTMLElement>('[data-testid="box-status-dot"]')!;
    expect(dot({ status: "online" }).className).toContain("online");
    const off = dot({ status: "offline", statusText: "No answer" });
    expect(off.className).toContain("offline");
    expect(off.getAttribute("title")).toBe("Offline: No answer");
    expect(dot({ status: "unknown" }).getAttribute("title")).toBe("");
  });

  it("the star asks to toggle the favourite", async () => {
    const { container, emitted } = mount({ isFavorite: false });
    await fireEvent.click(container.querySelector('[data-icon="PhStar"]')!.closest("button")!);
    expect(emitted()["toggle-favorite"]).toHaveLength(1);
  });
});
