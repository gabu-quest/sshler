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

function storedLocale(): Locale {
  const stored = (typeof localStorage !== "undefined" && localStorage.getItem(LOCALE_KEY)) as Locale | null;
  return stored === "ja" ? "ja" : "en";
}

// One locale for the whole page: the app's `t` and `translate` (used by code outside a
// component, as the request module's errors) read the same value.
const locale = ref<Locale>(storedLocale());

/** The text of `key` in the page's locale, else in English, else the key itself. */
export function translate(key: string, params?: Record<string, string | number>): string {
  const table = messages[locale.value] || messages.en;
  let result = table[key] ?? messages.en[key] ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      result = result.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
    }
  }
  return result;
}

export function createI18n(app: App) {
  locale.value = storedLocale();
  const t = translate;

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
