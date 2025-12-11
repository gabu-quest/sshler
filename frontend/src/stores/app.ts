import { computed, ref, watch, onMounted, onUnmounted } from "vue";

import { defineStore } from "pinia";

type ColorMode = "light" | "dark" | "system";

const COLOR_KEY = "sshler:ui:color-mode";
const REDUCED_MOTION_KEY = "sshler:ui:reduced-motion";

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

export const useAppStore = defineStore("app", () => {
  const colorMode = ref<ColorMode>(readStoredMode());
  const systemDark = ref(prefersDark());
  const reducedMotion = ref(readReducedMotionPreference());
  const isOnline = ref(typeof navigator !== "undefined" ? navigator.onLine : true);

  // Computed properties
  const isDark = computed(() => {
    if (colorMode.value === "system") {
      return systemDark.value;
    }
    return colorMode.value === "dark";
  });

  // Theme management
  const setColorMode = (mode: ColorMode) => {
    colorMode.value = mode;
  };

  const toggleTheme = () => {
    colorMode.value = isDark.value ? "light" : "dark";
  };

  const cycleTheme = () => {
    const modes: ColorMode[] = ["light", "dark", "system"];
    const currentIndex = modes.indexOf(colorMode.value);
    const nextIndex = (currentIndex + 1) % modes.length;
    colorMode.value = modes[nextIndex];
  };

  // Accessibility preferences
  const setReducedMotion = (enabled: boolean) => {
    reducedMotion.value = enabled;
  };

  const toggleReducedMotion = () => {
    reducedMotion.value = !reducedMotion.value;
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
  watch(
    colorMode,
    (value) => {
      if (typeof localStorage === "undefined") return;
      localStorage.setItem(COLOR_KEY, value);

      // Apply theme class to document for CSS custom properties
      document.documentElement.setAttribute("data-theme", isDark.value ? "dark" : "light");
    },
    { immediate: true },
  );

  watch(
    isDark,
    (value) => {
      if (typeof document === "undefined") return;
      document.documentElement.setAttribute("data-theme", value ? "dark" : "light");
    },
    { immediate: true },
  );

  watch(
    reducedMotion,
    (value) => {
      if (typeof localStorage === "undefined") return;
      localStorage.setItem(REDUCED_MOTION_KEY, value.toString());

      // Apply reduced motion class to document
      if (typeof document !== "undefined") {
        document.documentElement.classList.toggle("reduced-motion", value);
      }
    },
    { immediate: true },
  );

  // Initialize system listeners
  let cleanup: (() => void) | undefined;

  const init = () => {
    cleanup = setupMediaQueryListeners();

    // Set initial system values
    systemDark.value = prefersDark();

    // Apply initial classes
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", isDark.value ? "dark" : "light");
      document.documentElement.classList.toggle("reduced-motion", reducedMotion.value);
    }
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

    // Computed
    isDark,

    // Theme actions
    setColorMode,
    toggleTheme,
    cycleTheme,

    // Accessibility actions
    setReducedMotion,
    toggleReducedMotion,

    // Lifecycle
    init,
    destroy,
  };
});
