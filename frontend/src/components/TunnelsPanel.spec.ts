import { render, screen, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it, vi } from "vitest";

import { useTunnelsStore } from "@/stores/tunnels";
import TunnelsPanel from "./TunnelsPanel.vue";

// Real naive-ui cannot mount here (jsdom's cssstyle throws on its
// `border: var(--n-border)` rules), so the components are stubbed. The
// NDrawerContent stub renders exactly the slots naive-ui 2.43's DrawerContent
// renders (`header`, else the `title` prop; default; `footer`) and nothing else:
// that slot contract is what this spec checks the panel against.
vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NDrawer: stub('<div v-if="show"><slot /></div>', ["show"]),
    NDrawerContent: stub(
      '<section><header><slot name="header">{{ title }}</slot></header><slot /><footer><slot name="footer" /></footer></section>',
      ["title"],
    ),
    NButton: stub('<button type="button"><slot name="icon" /><slot /></button>'),
    NIcon: stub("<span><slot /></span>"),
    NInput: stub("<input />"),
    NInputNumber: stub("<input />"),
    NEmpty: stub('<div><slot name="extra" /></div>'),
    NSpace: stub("<div><slot /></div>"),
    NTag: stub("<span><slot /></span>"),
    NPopconfirm: stub('<span><slot name="trigger" /></span>'),
    NSpin: stub("<span />"),
    NRadioGroup: stub("<div><slot /></div>"),
    NRadio: stub("<label><slot /></label>"),
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
  };
});

describe("TunnelsPanel header add button", () => {
  // Mutation killed: the add button sits in NDrawerContent's `#header-extra`
  // slot, which naive-ui never renders (only header/default/footer), so a box
  // that already has a tunnel has no way to add another.
  it("opens the add form from the header when the box already has a tunnel", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useTunnelsStore();
    store.loadedBox = "devbox";
    store.items = [
      {
        id: "t1", box: "devbox", tunnel_type: "local",
        local_host: "127.0.0.1", local_port: 8080, remote_host: "127.0.0.1", remote_port: 80, created_at: 0,
      },
    ];

    render(TunnelsPanel, {
      props: { show: true, boxName: "devbox" },
      global: { plugins: [pinia] },
    });

    expect(document.body.querySelectorAll(".tunnel-form")).toHaveLength(0);
    await fireEvent.click(await screen.findByRole("button", { name: "Add tunnel" }));
    expect(document.body.querySelectorAll(".tunnel-form")).toHaveLength(1);
  });
});
