import { render } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import NotFoundView from "./NotFoundView.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, inheritAttrs: true, template });
  return {
    NButton: stub("<a class='stub-button'><slot /></a>", ["type", "text", "tag", "href"]),
    NCard: stub("<div><slot /></div>"),
  };
});

describe("NotFoundView", () => {
  it("shows the translated title, body and back link", () => {
    const { container } = render(NotFoundView, {
      global: { plugins: [{ install: (app: App) => createI18n(app) }] },
    });
    expect(container.querySelector("h2")!.textContent!.trim()).toBe("Page not found");
    expect(container.querySelector("p")!.textContent!.trim()).toBe("This page does not exist.");
    expect(container.querySelector(".stub-button")!.textContent!.trim()).toBe("Back to Overview");
  });
});
