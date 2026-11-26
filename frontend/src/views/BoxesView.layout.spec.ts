// The boxes page: box names are encoded in every link, and the card grid is one column on a
// phone, two on a tablet, three on a laptop.
import { fireEvent, render, waitFor } from "@testing-library/vue";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BoxesView from "./BoxesView.vue";

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "my box", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
  ]),
  fetchBox: vi.fn(),
  toggleFavorite: vi.fn(),
  togglePin: vi.fn(),
  boxStatus: vi.fn(),
}));

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NAlert: stub('<div><slot /></div>'),
    NButton: stub('<button class="stub-button" @click="$emit(\'click\')"><slot /></button>', [], ["click"]),
    NCard: stub('<div class="stub-card"><slot /></div>'),
    NGrid: stub("<div><slot /></div>"),
    NGridItem: stub("<div><slot /></div>"),
    NIcon: stub("<span />"),
    NSpace: stub("<div><slot /></div>"),
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
  };
});

describe("BoxesView links", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("encodes a box named 'my box' in the files, terminal and multi-terminal links", async () => {
    const push = vi.fn();
    const { container } = render(BoxesView, { global: { mocks: { $router: { push } } } });
    const buttons = await waitFor(() => {
      const found = Array.from(container.querySelectorAll(".stub-button"));
      expect(found.length).toBeGreaterThan(3);
      return found;
    });
    for (const label of ["files", "terminal", "multi"]) {
      await fireEvent.click(buttons.find((b) => b.textContent?.trim().toLowerCase() === label)!);
    }
    expect(push.mock.calls).toEqual([
      ["/files?box=my%20box"],
      ["/terminal?box=my%20box"],
      ["/multi-terminal?box=my%20box"],
    ]);
  });
});

describe("BoxesView card grid", () => {
  const css = readFileSync(resolve(__dirname, "BoxesView.vue"), "utf8");

  it("is one column by default, two from 640px, three from 1024px", () => {
    expect(css).toMatch(/\.box-grid\s*\{[^}]*grid-template-columns:\s*1fr\s*;/);
    expect(css).toMatch(/@media \(min-width: 640px\)\s*\{\s*\.box-grid\s*\{[^}]*repeat\(2,\s*1fr\)/);
    expect(css).toMatch(/@media \(min-width: 1024px\)\s*\{\s*\.box-grid\s*\{[^}]*repeat\(3,\s*1fr\)/);
  });
});
