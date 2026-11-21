import { render } from "@testing-library/vue";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ContextMenu from "./ContextMenu.vue";

// jsdom has no layout: the menu measures 200 by 300 and the window is 1000 by 600.
const MENU = { width: 200, height: 300 };
const WINDOW = { width: 1000, height: 600 };
const MARGIN = 8;

const items = [
  { id: "a", label: "Alpha" },
  { id: "b", label: "Beta" },
];

function menuElement(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>(".context-menu");
  if (!el) throw new Error("the menu is not open");
  return el;
}

function placed() {
  const el = menuElement();
  return { left: el.style.left, top: el.style.top };
}

function resizeWindow(width: number, height: number) {
  Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, value: width });
  Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: height });
  window.dispatchEvent(new Event("resize"));
}

async function open(x: number, y: number) {
  const view = render(ContextMenu, { props: { items, x: 0, y: 0, visible: false } });
  await view.rerender({ items, x, y, visible: true });
  await nextTick();
  return view;
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ ...MENU, x: 0, y: 0, top: 0, left: 0, right: MENU.width, bottom: MENU.height, toJSON: () => ({}) }),
  );
  Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, value: WINDOW.width });
  Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: WINDOW.height });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ContextMenu placement", () => {
  it("opens at the pointer when there is room", async () => {
    await open(100, 120);
    expect(placed()).toEqual({ left: "100px", top: "120px" });
  });

  it("moves left and up when the pointer is too near the right and bottom edges", async () => {
    await open(990, 590);
    expect(placed()).toEqual({
      left: `${WINDOW.width - MENU.width - MARGIN}px`,
      top: `${WINDOW.height - MENU.height - MARGIN}px`,
    });
  });

  it("keeps the margin from the top and left edges", async () => {
    await open(0, 0);
    expect(placed()).toEqual({ left: `${MARGIN}px`, top: `${MARGIN}px` });
  });

  it("measures again on a second open at another point", async () => {
    const view = await open(100, 100);
    await view.rerender({ items, x: 100, y: 100, visible: false });
    await view.rerender({ items, x: 990, y: 590, visible: true });
    await nextTick();
    expect(placed()).toEqual({
      left: `${WINDOW.width - MENU.width - MARGIN}px`,
      top: `${WINDOW.height - MENU.height - MARGIN}px`,
    });
  });

  it("places an open menu again when the pointer moves", async () => {
    const view = await open(100, 100);
    await view.rerender({ items, x: 500, y: 590, visible: true });
    await nextTick();
    expect(placed()).toEqual({ left: "500px", top: `${WINDOW.height - MENU.height - MARGIN}px` });
  });

  it("keeps an open menu inside when the window shrinks", async () => {
    await open(800, 400);
    resizeWindow(600, 450);
    await nextTick();
    expect(placed()).toEqual({
      left: `${600 - MENU.width - MARGIN}px`,
      top: `${450 - MENU.height - MARGIN}px`,
    });
  });
});
