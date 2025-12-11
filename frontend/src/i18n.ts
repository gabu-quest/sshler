import { computed, inject, ref, type App, type Ref } from "vue";

type Locale = "en" | "ja";

const messages: Record<Locale, Record<string, string>> = {
  en: {
    overview_heading: "FastAPI + Vue shell for sshler",
  },
  ja: {
    overview_heading: "sshler 向け FastAPI + Vue シェル",
  },
};

const LOCALE_KEY = "sshler:locale";
const I18N_KEY = Symbol("i18n");

type I18nContext = {
  locale: Ref<Locale>;
  t: (key: string) => string;
  setLocale: (value: Locale) => void;
};

export function createI18n(app: App) {
  const stored = (typeof localStorage !== "undefined" && localStorage.getItem(LOCALE_KEY)) as Locale | null;
  const locale = ref<Locale>(stored || "en");

  const t = (key: string) => {
    const table = messages[locale.value] || messages.en;
    return table[key] || key;
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
