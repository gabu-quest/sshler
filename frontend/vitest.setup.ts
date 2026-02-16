import "@testing-library/jest-dom";
import { vi } from "vitest";
import { ref, computed } from "vue";
import { en } from "@/locales/en";

// Mock i18n globally so all components can call useI18n() without provider setup.
// Returns a real translation function backed by the English locale.
vi.mock("@/i18n", () => {
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
