import { render, screen } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { defineComponent } from "vue";

vi.mock("naive-ui", () => {
  const Stub = defineComponent({ template: "<div><slot /></div>" });
  return {
    NAlert: Stub,
    NCard: Stub,
    NDivider: Stub,
    NIcon: Stub,
    NList: Stub,
    NListItem: Stub,
    NSelect: Stub,
    NTag: Stub,
    NButton: Stub,
    NSpin: Stub,
    useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
  };
});

import TerminalView from "./TerminalView.vue";

describe("TerminalView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders headings", () => {
    render(TerminalView, {
      global: {
        stubs: {
          NAlert: { template: "<div><slot /></div>" },
          NCard: { template: "<div><slot /></div>" },
          NDivider: { template: "<div><slot /></div>" },
          NIcon: { template: "<span><slot /></span>" },
          NList: { template: "<ul><slot /></ul>" },
          NListItem: { template: "<li><slot /></li>" },
          NSelect: { template: "<select><slot /></select>" },
          NTag: { template: "<span><slot /></span>" },
          NButton: { template: "<button><slot /></button>" },
          NSpin: { template: "<span><slot /></span>" },
        },
      },
    });
    expect(screen.getAllByText(/terminal/i).length).toBeGreaterThan(0);
  });
});
