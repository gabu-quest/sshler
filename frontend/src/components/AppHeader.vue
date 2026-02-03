<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { NButton, NIcon, NSpace, NDrawer, NDrawerContent, NTooltip } from "naive-ui";
import {
  PhFolderSimple,
  PhGearSix,
  PhHouseLine,
  PhMoon,
  PhSun,
  PhTerminal,
  PhList,
  PhX,
  PhLockKey,
} from "@phosphor-icons/vue";

import { useAppStore } from "@/stores/app";
import { useAuthStore } from "@/stores/auth";
import { useBootstrapStore } from "@/stores/bootstrap";
import CommandPalette from "@/components/CommandPalette.vue";
import ShortcutsOverlay from "@/components/ShortcutsOverlay.vue";

const appStore = useAppStore();
const authStore = useAuthStore();
const bootstrapStore = useBootstrapStore();
const route = useRoute();

// Auth status computed
const showAuthIndicator = computed(() => bootstrapStore.basicAuthRequired && authStore.isAuthenticated);
const authTooltip = computed(() => {
  if (!bootstrapStore.basicAuthRequired) return '';
  return authStore.isAuthenticated
    ? `Authenticated as ${authStore.displayUsername}`
    : 'Authentication required';
});

// Mobile navigation state
const isMobileMenuOpen = ref(false);
const isMobile = ref(false);

const links = [
  { to: "/", label: "Overview", icon: PhHouseLine, shortcut: "Alt+H" },
  { to: "/boxes", label: "Boxes", icon: PhFolderSimple, shortcut: "Alt+B" },
  { to: "/files", label: "Files", icon: PhFolderSimple, shortcut: "Alt+F" },
  { to: "/terminal", label: "Terminal", icon: PhTerminal, shortcut: "Alt+T" },
  { to: "/multi-terminal", label: "Multi-Terminal", icon: PhTerminal, shortcut: "Alt+M" },
  { to: "/settings", label: "Settings", icon: PhGearSix, shortcut: "Alt+S" },
];

const themeIcon = computed(() => (appStore.isDark ? PhMoon : PhSun));
const themeLabel = computed(() => {
  if (appStore.colorMode === "system") {
    return `system (${appStore.isDark ? "dark" : "light"})`;
  }
  return appStore.colorMode;
});

const isActive = (path: string) => {
  if (path === "/") {
    return route.path === "/";
  }
  return route.path.startsWith(path);
};

const toggleTheme = () => {
  appStore.toggleTheme();
};

const toggleMobileMenu = () => {
  isMobileMenuOpen.value = !isMobileMenuOpen.value;
};

const closeMobileMenu = () => {
  isMobileMenuOpen.value = false;
};

// Handle responsive behavior
const checkMobile = () => {
  isMobile.value = window.innerWidth < 768;
  if (!isMobile.value) {
    isMobileMenuOpen.value = false;
  }
};

// Handle keyboard shortcuts
const handleKeydown = (event: KeyboardEvent) => {
  if (event.altKey) {
    const shortcutMap: Record<string, string> = {
      'h': '/',
      'b': '/boxes',
      'f': '/files',
      't': '/terminal',
      'm': '/multi-terminal',
      's': '/settings',
    };
    
    const path = shortcutMap[event.key.toLowerCase()];
    if (path) {
      event.preventDefault();
      // Use router push instead of direct navigation for better UX
      const router = useRoute().matched[0]?.instances?.default?.$router;
      if (router) {
        router.push(path);
      }
    }
  }
};

onMounted(() => {
  checkMobile();
  window.addEventListener('resize', checkMobile);
  window.addEventListener('keydown', handleKeydown);
});

onUnmounted(() => {
  window.removeEventListener('resize', checkMobile);
  window.removeEventListener('keydown', handleKeydown);
});
</script>

<template>
  <header class="app-header surface" role="banner">
    <!-- Brand -->
    <RouterLink
      class="brand"
      to="/"
      aria-label="sshler home"
      @click="closeMobileMenu"
    >
      <img src="/logo.png" alt="sshler" class="brand-logo" />
    </RouterLink>

    <!-- Desktop Navigation -->
    <nav class="nav desktop-nav" role="navigation" aria-label="Main navigation">
      <RouterLink
        v-for="link in links"
        :key="link.to"
        :to="link.to"
        class="nav-link"
        :class="{ active: isActive(link.to) }"
        :aria-label="`${link.label} (${link.shortcut})`"
        :title="`${link.label} - ${link.shortcut}`"
      >
        <NIcon size="16" aria-hidden="true">
          <component :is="link.icon" />
        </NIcon>
        <span>{{ link.label }}</span>
      </RouterLink>
    </nav>

    <!-- Header Actions -->
    <div class="header-actions">
      <NSpace align="center" size="small">
        <!-- Auth Indicator -->
        <NTooltip v-if="showAuthIndicator" :delay="300">
          <template #trigger>
            <div class="auth-indicator" :aria-label="authTooltip">
              <NIcon size="16" color="var(--success-color)">
                <PhLockKey />
              </NIcon>
              <span class="auth-username">{{ authStore.displayUsername }}</span>
            </div>
          </template>
          {{ authTooltip }}
        </NTooltip>

        <span class="text-muted mode-label" aria-live="polite">{{ themeLabel }}</span>
        <NButton
          quaternary
          circle
          size="small"
          @click="toggleTheme"
          :aria-label="`Switch to ${appStore.isDark ? 'light' : 'dark'} theme`"
          title="Toggle theme"
        >
          <NIcon size="18" aria-hidden="true">
            <component :is="themeIcon" />
          </NIcon>
        </NButton>
        <CommandPalette />
        <ShortcutsOverlay />
        
        <!-- Mobile Menu Button -->
        <NButton 
          v-if="isMobile"
          quaternary 
          circle 
          size="small" 
          @click="toggleMobileMenu"
          :aria-label="isMobileMenuOpen ? 'Close menu' : 'Open menu'"
          :aria-expanded="isMobileMenuOpen"
          class="mobile-menu-button"
        >
          <NIcon size="18" aria-hidden="true">
            <component :is="isMobileMenuOpen ? PhX : PhList" />
          </NIcon>
        </NButton>
      </NSpace>
    </div>

    <!-- Mobile Navigation Drawer -->
    <NDrawer 
      v-model:show="isMobileMenuOpen" 
      placement="right" 
      :width="280"
      :trap-focus="false"
      :block-scroll="false"
    >
      <NDrawerContent title="Navigation" closable>
        <nav class="mobile-nav" role="navigation" aria-label="Mobile navigation">
          <RouterLink
            v-for="link in links"
            :key="link.to"
            :to="link.to"
            class="mobile-nav-link"
            :class="{ active: isActive(link.to) }"
            @click="closeMobileMenu"
            :aria-label="link.label"
          >
            <NIcon size="20" aria-hidden="true">
              <component :is="link.icon" />
            </NIcon>
            <div class="mobile-nav-content">
              <span class="mobile-nav-label">{{ link.label }}</span>
              <span class="mobile-nav-shortcut text-muted">{{ link.shortcut }}</span>
            </div>
          </RouterLink>
        </nav>
      </NDrawerContent>
    </NDrawer>
  </header>
</template>

<style scoped>
.app-header {
  display: grid;
  align-items: center;
  grid-template-columns: auto 1fr auto;
  gap: 16px;
  padding: 12px 16px;
  backdrop-filter: blur(10px);
  position: relative;
  z-index: 100;
}

.brand {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-decoration: none;
  transition: opacity 0.2s ease;
}

.brand:hover {
  opacity: 0.8;
}

.brand:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 4px;
}

.brand-logo {
  height: 32px;
  width: auto;
  object-fit: contain;
  border-radius: 6px;
}

/* Desktop Navigation */
.desktop-nav {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px;
}

.nav-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 10px;
  color: var(--muted);
  border: 1px solid transparent;
  transition: all 0.2s ease;
  text-decoration: none;
  min-height: 44px; /* Touch target size */
  position: relative;
}

.nav-link:hover {
  color: var(--text);
  border-color: var(--stroke);
  background: rgba(255, 255, 255, 0.04);
}

.nav-link:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.nav-link.active {
  color: var(--text);
  border-color: var(--accent);
  background: rgba(124, 93, 255, 0.12);
}

.nav-link.active::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 50%;
  transform: translateX(-50%);
  width: 20px;
  height: 2px;
  background: var(--accent);
  border-radius: 1px;
}

/* Header Actions */
.header-actions {
  display: flex;
  align-items: center;
}

.auth-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  background: rgba(var(--success-color-rgb, 24, 160, 88), 0.1);
  border: 1px solid rgba(var(--success-color-rgb, 24, 160, 88), 0.2);
}

.auth-username {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mode-label {
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.3px;
  white-space: nowrap;
}

.mobile-menu-button {
  display: none;
}

/* Mobile Navigation */
.mobile-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 0;
}

.mobile-nav-link {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 20px;
  border-radius: 12px;
  color: var(--muted);
  text-decoration: none;
  transition: all 0.2s ease;
  min-height: 44px; /* Touch target size */
}

.mobile-nav-link:hover,
.mobile-nav-link:focus-visible {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}

.mobile-nav-link.active {
  color: var(--text);
  background: rgba(124, 93, 255, 0.12);
  border-left: 3px solid var(--accent);
}

.mobile-nav-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.mobile-nav-label {
  font-weight: 500;
  font-size: 16px;
}

.mobile-nav-shortcut {
  font-size: 12px;
  opacity: 0.7;
}

/* Responsive Design */
@media (max-width: 768px) {
  .app-header {
    grid-template-columns: auto 1fr auto;
    padding: 12px 16px;
  }

  .desktop-nav {
    display: none;
  }

  .mobile-menu-button {
    display: flex;
  }

  .mode-label {
    display: none;
  }

  .auth-username {
    display: none;
  }

  .auth-indicator {
    padding: 6px 8px;
  }
}

@media (max-width: 480px) {
  .app-header {
    padding: 8px 12px;
    gap: 12px;
  }

  .brand-mark {
    gap: 6px;
  }

  .brand-mark span {
    font-size: 14px;
  }
}

/* Reduced Motion Support */
@media (prefers-reduced-motion: reduce) {
  .nav-link,
  .mobile-nav-link,
  .brand {
    transition: none;
  }
}

/* High Contrast Support */
@media (prefers-contrast: high) {
  .nav-link {
    border-color: var(--text);
  }
  
  .nav-link.active {
    background: var(--accent);
    color: white;
  }
}

/* Focus Management */
.nav-link:focus-visible,
.mobile-nav-link:focus-visible,
.brand:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 4px;
}
</style>
