import "@testing-library/jest-dom";
import { vi } from "vitest";

// Mock i18n globally so all components can call useI18n() without provider setup.
// Uses async factory with dynamic imports to avoid vi.mock hoisting issues
// (vi.mock is hoisted above imports, so static refs to `ref`/`computed`/`en` would be undefined).
vi.mock("@/i18n", async () => {
  const { ref, computed } = await import("vue");
  const { en } = await import("@/locales/en");

  const locale = ref("en");
  const t = (key: string, params?: Record<string, string | number>): string => {
    let result = en[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        result = result.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
      }
    }
    return result;
  };
  return {
    useI18n: () => ({
      locale: computed(() => locale.value),
      t,
      setLocale: (v: string) => { locale.value = v as any; },
    }),
    createI18n: vi.fn(),
    availableLocales: [
      { label: "English", value: "en" },
      { label: "日本語", value: "ja" },
    ],
  };
});
