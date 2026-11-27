import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import MobileInputBar from "./MobileInputBar.vue";

// Every text comes back as its key, so a literal left in the bar shows as itself.
vi.mock("@/i18n", async () => {
  const { computed, ref } = await import("vue");
  const locale = ref("en");
  const t = (key: string) => key;
  return {
    translate: t,
    useI18n: () => ({ locale: computed(() => locale.value), t, setLocale: () => {} }),
    createI18n: vi.fn(),
    availableLocales: [{ label: "English", value: "en" }],
  };
});

describe("MobileInputBar: every key is named through its text", () => {
  // Mutation: the English titles kept in the key table -> "Up", "Down", ... in Japanese too.
  it("a tmux bar's keys and the legend button carry their keys' texts, in order", () => {
    const wrapper = mount(MobileInputBar, { props: { connected: true } });
    expect(wrapper.findAll("button.quick-key").map((b) => b.attributes("title"))).toEqual([
      "mobile.key_up", "mobile.key_down", "mobile.key_left", "mobile.key_right", "mobile.key_enter",
      "mobile.key_tab", "mobile.key_escape", "mobile.key_ctrlc", "mobile.key_scroll", "mobile.key_pgup",
      "mobile.key_pgdn", "mobile.key_ctrld", "mobile.key_prev_window", "mobile.key_new_window",
      "mobile.key_next_window", "mobile.key_split_v", "mobile.key_split_h", "mobile.show_legend",
    ]);
  });

  // The fixed colour meanings stay: red is Ctrl+C, and only it.
  it("the red key is Ctrl+C alone", () => {
    const wrapper = mount(MobileInputBar, { props: { connected: true } });
    expect(wrapper.findAll("button.quick-key.red").map((b) => b.attributes("title"))).toEqual(["mobile.key_ctrlc"]);
  });
});
