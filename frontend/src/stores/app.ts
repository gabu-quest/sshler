import { computed, ref, watch } from "vue";

import { defineStore } from "pinia";

type ColorMode = "light" | "dark" | "system";

const COLOR_KEY = "sshler:ui:color-mode";
const REDUCED_MOTION_KEY = "sshler:ui:reduced-motion";
const TERMINAL_FONT_SIZE_KEY = "sshler:terminal:font-size";
const TERMINAL_FONT_FAMILY_KEY = "sshler:terminal:font-family";
const TERMINAL_SCROLLBACK_KEY = "sshler:terminal:scrollback";

const prefersDark = () => {
  if (typeof window === "undefined") {
    return false;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
};

const prefersReducedMotion = () => {
  if (typeof window === "undefined") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
};

const readStoredMode = (): ColorMode => {
  if (typeof localStorage === "undefined") {
    return "system";
  }
  const stored = localStorage.getItem(COLOR_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") {
    return stored;
  }
  return "system";
};

const readReducedMotionPreference = (): boolean => {
  if (typeof localStorage === "undefined") {
    return prefersReducedMotion();
  }
  const stored = localStorage.getItem(REDUCED_MOTION_KEY);
  if (stored === "true" || stored === "false") {
    return stored === "true";
  }
  return prefersReducedMotion();
};

const DEFAULT_FONT_FAMILY = '"Monaspace Neon", "CaskaydiaCove Nerd Font", "JetBrains Mono Nerd Font", "FiraCode Nerd Font", "Symbols Nerd Font Mono", "JetBrains Mono", "Fira Code", monospace';

const readTerminalFontSize = (): number => {
  if (typeof localStorage === "undefined") return 14;
  const stored = localStorage.getItem(TERMINAL_FONT_SIZE_KEY);
  if (stored) {
    const n = parseInt(stored, 10);
    if (n >= 8 && n <= 24) return n;
  }
  return 14;
};

const readTerminalFontFamily = (): string => {
  if (typeof localStorage === "undefined") return DEFAULT_FONT_FAMILY;
  return localStorage.getItem(TERMINAL_FONT_FAMILY_KEY) || DEFAULT_FONT_FAMILY;
};

const readTerminalScrollback = (): number => {
  if (typeof localStorage === "undefined") return 10000;
  const stored = localStorage.getItem(TERMINAL_SCROLLBACK_KEY);
  if (stored) {
    const n = parseInt(stored, 10);
    if (n >= 500 && n <= 50000) return n;
  }
  return 10000;
};

export const useAppStore = defineStore("app", () => {
  const colorMode = ref<ColorMode>(readStoredMode());
  const systemDark = ref(prefersDark());
  const reducedMotion = ref(readReducedMotionPreference());
  const isOnline = ref(typeof navigator !== "undefined" ? navigator.onLine : true);

  // Terminal settings
  const terminalFontSize = ref(readTerminalFontSize());
  const terminalFontFamily = ref(readTerminalFontFamily());
  const terminalScrollback = ref(readTerminalScrollback());

  // Active box tracking (which box the user is currently viewing)
  const activeBox = ref<string | null>(null);

  // Computed properties
  const isDark = computed(() => {
    if (colorMode.value === "system") {
      return systemDark.value;
    }
    return colorMode.value === "dark";
  });

  const applyThemeAttributes = () => {
    if (typeof document === "undefined") return;
    document.documentElement.setAttribute("data-theme", isDark.value ? "dark" : "light");
    document.documentElement.classList.toggle("reduced-motion", reducedMotion.value);
  };

  // Theme management
  const setColorMode = (mode: ColorMode) => {
    colorMode.value = mode;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(COLOR_KEY, mode);
    }
    applyThemeAttributes();
  };

  const toggleTheme = () => {
    setColorMode(isDark.value ? "light" : "dark");
  };

  const cycleTheme = () => {
    const modes: ColorMode[] = ["light", "dark", "system"];
    const currentIndex = modes.indexOf(colorMode.value);
    const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % modes.length;
    setColorMode(modes[nextIndex] as ColorMode);
  };

  // Terminal settings
  const setTerminalFontSize = (size: number) => {
    const clamped = Math.max(8, Math.min(24, size));
    terminalFontSize.value = clamped;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(TERMINAL_FONT_SIZE_KEY, clamped.toString());
    }
  };

  const setTerminalFontFamily = (family: string) => {
    terminalFontFamily.value = family;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(TERMINAL_FONT_FAMILY_KEY, family);
    }
  };

  const setTerminalScrollback = (lines: number) => {
    const clamped = Math.max(500, Math.min(50000, lines));
    terminalScrollback.value = clamped;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(TERMINAL_SCROLLBACK_KEY, clamped.toString());
    }
  };

  // Accessibility preferences
  const setReducedMotion = (enabled: boolean) => {
    reducedMotion.value = enabled;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(REDUCED_MOTION_KEY, enabled.toString());
    }
    applyThemeAttributes();
  };

  const toggleReducedMotion = () => {
    setReducedMotion(!reducedMotion.value);
  };

  // System preference listeners
  let darkModeMediaQuery: MediaQueryList | null = null;
  let reducedMotionMediaQuery: MediaQueryList | null = null;

  const setupMediaQueryListeners = () => {
    if (typeof window === "undefined") return;

    // Dark mode listener
    darkModeMediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleDarkModeChange = (e: MediaQueryListEvent) => {
      systemDark.value = e.matches;
      applyThemeAttributes();
    };
    darkModeMediaQuery.addEventListener("change", handleDarkModeChange);

    // Reduced motion listener
    reducedMotionMediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleReducedMotionChange = (e: MediaQueryListEvent) => {
      if (localStorage.getItem(REDUCED_MOTION_KEY) === null) {
        reducedMotion.value = e.matches;
      }
    };
    reducedMotionMediaQuery.addEventListener("change", handleReducedMotionChange);

    // Online/offline listeners
    const handleOnline = () => { isOnline.value = true; };
    const handleOffline = () => { isOnline.value = false; };
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      darkModeMediaQuery?.removeEventListener("change", handleDarkModeChange);
      reducedMotionMediaQuery?.removeEventListener("change", handleReducedMotionChange);
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  };

  // Persistence watchers
  watch(colorMode, () => {
    applyThemeAttributes();
  });

  watch(isDark, () => {
    applyThemeAttributes();
  });

  watch(systemDark, () => {
    if (colorMode.value === "system") {
      applyThemeAttributes();
    }
  });

  watch(reducedMotion, () => {
    applyThemeAttributes();
  });

  // Initialize system listeners
  let cleanup: (() => void) | undefined;

  const init = () => {
    cleanup = setupMediaQueryListeners();

    // Set initial system values
    systemDark.value = prefersDark();

    applyThemeAttributes();
  };

  const destroy = () => {
    cleanup?.();
  };

  return {
    // State
    colorMode,
    systemDark,
    reducedMotion,
    isOnline,

    // Active box
    activeBox,

    // Terminal settings
    terminalFontSize,
    terminalFontFamily,
    terminalScrollback,

    // Computed
    isDark,

    // Theme actions
    setColorMode,
    toggleTheme,
    cycleTheme,

    // Terminal settings actions
    setTerminalFontSize,
    setTerminalFontFamily,
    setTerminalScrollback,

    // Accessibility actions
    setReducedMotion,
    toggleReducedMotion,

    // Lifecycle
    init,
    destroy,
  };
});
