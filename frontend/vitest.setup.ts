import "@testing-library/jest-dom";
import { vi, beforeEach } from "vitest";

// jsdom 27 (shipped with vitest 4) exposes a broken localStorage stub where
// getItem/setItem are not functions. Replace it with a real in-memory mock.
const _makeStorage = () => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = String(value); },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
};
Object.defineProperty(globalThis, "localStorage", { value: _makeStorage(), writable: true });
Object.defineProperty(globalThis, "sessionStorage", { value: _makeStorage(), writable: true });

beforeEach(() => {
  (globalThis.localStorage as any).clear();
  (globalThis.sessionStorage as any).clear();
});

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
