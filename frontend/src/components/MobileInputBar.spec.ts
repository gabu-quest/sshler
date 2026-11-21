import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import MobileInputBar from "./MobileInputBar.vue";

const OTHER_KEYS = [
  "Up", "Down", "Left", "Right", "Enter", "Tab", "Escape / Stop", "Interrupt (Ctrl+C)",
  "Page Up", "Page Down", "Exit (Ctrl+D)",
];

const LEGEND_BUTTON = "Show key legend";
const keyTitles = (w: ReturnType<typeof mount>) =>
  w.findAll("button.quick-key").map((b) => b.attributes("title")).filter((t) => t !== LEGEND_BUTTON);
const legendLabels = () => [...document.body.querySelectorAll(".legend-label")].map((l) => l.textContent);

describe("MobileInputBar tmux keys", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("a tmux terminal (the default) renders every key in order, and the divider", () => {
    const wrapper = mount(MobileInputBar, { props: { connected: true } });

    expect(keyTitles(wrapper)).toEqual([
      ...OTHER_KEYS.slice(0, 8), "Scroll mode", ...OTHER_KEYS.slice(8),
      "Prev window", "New window", "Next window", "Split vertical", "Split horizontal",
    ]);
    expect(wrapper.findAll(".quick-key-divider")).toHaveLength(1);
  });

  it("a plain terminal renders exactly the non-tmux keys and no divider", () => {
    const wrapper = mount(MobileInputBar, { props: { connected: true, tmux: false } });

    expect(keyTitles(wrapper)).toEqual(OTHER_KEYS);
    expect(wrapper.findAll(".quick-key-divider")).toHaveLength(0);
  });

  it("pressing every key of a plain terminal sends exactly these bytes, no Ctrl+B among them", async () => {
    const wrapper = mount(MobileInputBar, { props: { connected: true, tmux: false } });
    for (const b of wrapper.findAll("button.quick-key")) {
      if (b.attributes("title") !== LEGEND_BUTTON) await b.trigger("click");
    }

    expect((wrapper.emitted("send-raw") ?? []).map((a) => a[0])).toEqual([
      "\x1b[A", "\x1b[B", "\x1b[D", "\x1b[C", "\r", "\t", "\x1b", "\x03", "\x1b[5~", "\x1b[6~", "\x04",
    ]);
  });

  it("the legend of a plain terminal has exactly the non-tmux rows", async () => {
    const wrapper = mount(MobileInputBar, { props: { connected: true, tmux: false } });
    await wrapper.find(`button[title='${LEGEND_BUTTON}']`).trigger("click");

    expect(legendLabels()).toEqual(OTHER_KEYS);
    wrapper.unmount();
  });

  it("the legend of a tmux terminal has every row", async () => {
    const wrapper = mount(MobileInputBar, { props: { connected: true } });
    await wrapper.find(`button[title='${LEGEND_BUTTON}']`).trigger("click");

    expect(legendLabels()).toEqual([
      ...OTHER_KEYS.slice(0, 8), "Scroll mode", ...OTHER_KEYS.slice(8),
      "Prev window", "New window", "Next window", "Split vertical", "Split horizontal",
    ]);
    wrapper.unmount();
  });
});
