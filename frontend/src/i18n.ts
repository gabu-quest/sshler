import { computed, inject, ref, type App, type Ref } from "vue";
import { en } from "@/locales/en";
import { ja } from "@/locales/ja";

export type Locale = "en" | "ja";

export const availableLocales: { label: string; value: Locale }[] = [
  { label: "English", value: "en" },
  { label: "日本語", value: "ja" },
];

const messages: Record<Locale, Record<string, string>> = { en, ja };

const LOCALE_KEY = "sshler:locale";
const I18N_KEY = Symbol("i18n");

type I18nContext = {
  locale: Ref<Locale>;
  t: (key: string, params?: Record<string, string | number>) => string;
  setLocale: (value: Locale) => void;
};

export function createI18n(app: App) {
  const stored = (typeof localStorage !== "undefined" && localStorage.getItem(LOCALE_KEY)) as Locale | null;
  const locale = ref<Locale>(stored === "ja" ? "ja" : "en");

  const t = (key: string, params?: Record<string, string | number>): string => {
    const table = messages[locale.value] || messages.en;
    let result = table[key] ?? messages.en[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        result = result.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
      }
    }
    return result;
  };

  const setLocale = (value: Locale) => {
    locale.value = value;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(LOCALE_KEY, value);
    }
  };

  const ctx: I18nContext = { locale, t, setLocale };
  app.provide(I18N_KEY, ctx);
  return { locale: computed(() => locale.value), t, setLocale };
}

export function useI18n() {
  const ctx = inject<I18nContext | null>(I18N_KEY, null);
  if (!ctx) {
    throw new Error("i18n not initialised");
  }
  return {
    locale: computed(() => ctx.locale.value),
    t: ctx.t,
    setLocale: ctx.setLocale,
  };
}
