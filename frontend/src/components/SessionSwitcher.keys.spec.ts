import { render, waitFor } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";

import type { ApiSession } from "@/api/types";
import SessionSwitcher from "./SessionSwitcher.vue";

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

const { recovered } = vi.hoisted(() => ({ recovered: {
  id: "s1", box: "build-box", session_name: "api", working_directory: "/srv/work",
  created_at: 0, last_accessed_at: 0, active: true, window_count: 1,
  metadata: { recovered_at: 1 }, restorable: false,
} as ApiSession }));

vi.mock("@/api/http", () => ({
  fetchBoxSessions: vi.fn(async () => [recovered]),
  syncBoxSessions: vi.fn(async () => [recovered]),
  deleteSession: vi.fn(),
  renameSession: vi.fn(),
  recreateSession: vi.fn(),
}));

vi.mock("naive-ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("naive-ui")>()),
  useMessage: () => ({ error: vi.fn(), success: vi.fn() }),
}));

describe("SessionSwitcher: the recovered mark", () => {
  // Mutation: the literal "Recovered from crash" kept -> the Japanese page shows English.
  it("is named through its text", async () => {
    const { container } = render(SessionSwitcher, { props: { boxName: "build-box", token: "t" } });
    await waitFor(() => expect(container.querySelectorAll(".recovered-badge")).toHaveLength(1));
    expect(container.querySelector(".recovered-badge")!.getAttribute("title")).toBe("sessions.recovered_tooltip");
  });
});
