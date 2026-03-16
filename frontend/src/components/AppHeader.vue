<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { NButton, NIcon, NSpace, NDrawer, NDrawerContent, NTooltip, NProgress, NSelect } from "naive-ui";
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
  PhCpu,
  PhMemory,
  PhWifiSlash,
} from "@phosphor-icons/vue";

import { useAppStore } from "@/stores/app";
import { useAuthStore } from "@/stores/auth";
import { getEmojiForBox } from "@/utils/emoji-favicon";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useResponsive } from "@/composables/useResponsive";
import { useI18n, availableLocales } from "@/i18n";
import type { Locale } from "@/i18n";
import { boxStats, fetchSnapshotStatus } from "@/api/http";
import type { BoxStats } from "@/api/types";
import CommandPalette from "@/components/CommandPalette.vue";
import ShortcutsOverlay from "@/components/ShortcutsOverlay.vue";

const appStore = useAppStore();
const authStore = useAuthStore();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const route = useRoute();
const { isMobile } = useResponsive();
const { t, locale, setLocale } = useI18n();

const localeOptions = availableLocales.map(l => ({ label: l.label, value: l.value }));
const currentLocale = computed({
  get: () => locale.value,
  set: (v: Locale) => setLocale(v),
});

// System stats for local box (shown in header)
const localStats = ref<BoxStats | null>(null);
const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token);

// Snapshot status for the indicator dot
const lastSnapshotAt = ref<number | null>(null);
const now = ref(Date.now());
let snapshotTickInterval: number | null = null;
const snapshotFreshness = computed(() => {
  if (!lastSnapshotAt.value) return 0;
  const elapsed = now.value / 1000 - lastSnapshotAt.value;
  return Math.max(0, 1 - elapsed / 30);
});
const snapshotDotStyle = computed(() => {
  const f = snapshotFreshness.value;
  if (f <= 0) {
    return {
      background: 'rgba(100, 100, 110, 0.4)',
      boxShadow: 'none',
    };
  }
  // Bright blue at f=1, fading to grey at f=0
  const color = `rgba(56, 140, 255, ${f})`;
  const glowSize = Math.round(4 + 6 * f);
  return {
    background: color,
    boxShadow: `0 0 ${glowSize}px rgba(56, 140, 255, ${f * 0.8})`,
  };
});
const snapshotTooltip = computed(() => {
  if (!lastSnapshotAt.value) return 'No snapshots yet';
  const elapsed = Math.floor(Date.now() / 1000 - lastSnapshotAt.value);
  return `Session snapshot: ${elapsed}s ago`;
});

async function loadSnapshotStatus() {
  try {
    const status = await fetchSnapshotStatus(tokenValue.value || null);
    lastSnapshotAt.value = status.last_snapshot_at;
  } catch {
    // Best effort
  }
}

async function loadLocalStats() {
  try {
    const stats = await boxStats('local', tokenValue.value || null);
    localStats.value = stats;
  } catch (e) {
    // Stats are optional
  }
}

// Color helpers for stats
function getStatColor(percent: number | null | undefined, normalColor: string): string {
  const p = percent ?? 0;
  if (p >= 90) return '#ef4444'; // Red - critical
  if (p >= 80) return '#f97316'; // Orange - warning
  return normalColor;
}

// Green/orange/red scale for mobile stats
function getStatColorGreen(percent: number | null | undefined): string {
  const p = percent ?? 0;
  if (p >= 90) return '#ef4444'; // Red - critical
  if (p >= 75) return '#f97316'; // Orange - warning
  return '#22c55e'; // Green - good
}

function isStatCritical(percent: number | null | undefined): boolean {
  return (percent ?? 0) >= 90;
}

// Refresh stats periodically
let statsInterval: number | null = null;

// Auth status computed
const showAuthIndicator = computed(() => bootstrapStore.basicAuthRequired && authStore.isAuthenticated);
const authTooltip = computed(() => {
  if (!bootstrapStore.basicAuthRequired) return '';
  return authStore.isAuthenticated
    ? t('header.authenticated_as', { user: authStore.displayUsername })
    : t('header.auth_required');
});

// Mobile navigation state
const isMobileMenuOpen = ref(false);

const links = computed(() => {
  const box = appStore.activeBox;
  const boxQuery = box ? `?box=${encodeURIComponent(box)}` : '';
  return [
    { to: "/", label: t("nav.overview"), icon: PhHouseLine, shortcut: "Alt+H" },
    { to: "/boxes", label: t("nav.boxes"), icon: PhFolderSimple, shortcut: "Alt+B" },
    { to: `/files${boxQuery}`, label: t("nav.files"), icon: PhFolderSimple, shortcut: "Alt+F" },
    { to: `/terminal${boxQuery}`, label: t("nav.terminal"), icon: PhTerminal, shortcut: "Alt+T" },
    { to: `/multi-terminal${boxQuery}`, label: t("nav.multi_terminal"), icon: PhTerminal, shortcut: "Alt+M" },
    { to: "/settings", label: t("nav.settings"), icon: PhGearSix, shortcut: "Alt+S" },
  ];
});

const activeBoxEmoji = computed(() => appStore.activeBox ? getEmojiForBox(appStore.activeBox) : null);

const themeIcon = computed(() => (appStore.isDark ? PhMoon : PhSun));
const themeLabel = computed(() => {
  if (appStore.colorMode === "system") {
    return `system (${appStore.isDark ? "dark" : "light"})`;
  }
  return appStore.colorMode;
});

const isActive = (path: string) => {
  const basePath = path.split('?')[0];
  if (basePath === "/") {
    return route.path === "/";
  }
  return route.path.startsWith(basePath);
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

// Close mobile menu when switching to desktop
watch(isMobile, (mobile) => {
  if (!mobile) isMobileMenuOpen.value = false;
});

// Handle keyboard shortcuts
const handleKeydown = (event: KeyboardEvent) => {
  if (event.altKey) {
    const box = appStore.activeBox;
    const boxQuery = box ? `?box=${encodeURIComponent(box)}` : '';
    const shortcutMap: Record<string, string> = {
      'h': '/',
      'b': '/boxes',
      'f': `/files${boxQuery}`,
      't': `/terminal${boxQuery}`,
      'm': `/multi-terminal${boxQuery}`,
      's': '/settings',
    };

    const path = shortcutMap[event.key.toLowerCase()];
    if (path) {
      event.preventDefault();
      const router = useRoute().matched[0]?.instances?.default?.$router;
      if (router) {
        router.push(path);
      }
    }
  }
};

// Only poll stats when the tab is visible (saves requests when 20+ tabs open)
const startStatsPolling = () => {
  if (statsInterval) return;
  loadLocalStats();
  loadSnapshotStatus();
  statsInterval = window.setInterval(() => {
    loadLocalStats();
    loadSnapshotStatus();
  }, 10000);
};

const stopStatsPolling = () => {
  if (statsInterval) {
    clearInterval(statsInterval);
    statsInterval = null;
  }
};

const handleVisibilityChange = () => {
  if (document.hidden) {
    stopStatsPolling();
  } else {
    startStatsPolling();
  }
};

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
  document.addEventListener('visibilitychange', handleVisibilityChange);
  snapshotTickInterval = window.setInterval(() => { now.value = Date.now(); }, 2000);

  if (!document.hidden) {
    startStatsPolling();
  }
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
  document.removeEventListener('visibilitychange', handleVisibilityChange);
  stopStatsPolling();
  if (snapshotTickInterval) clearInterval(snapshotTickInterval);
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
          <component :is="link.icon" weight="duotone" />
        </NIcon>
        <span>{{ link.label }}</span>
      </RouterLink>
      <span v-if="appStore.activeBox" class="active-box-pill" :title="t('nav.active_box', { box: appStore.activeBox })">
        {{ activeBoxEmoji }} {{ appStore.activeBox }}
      </span>
    </nav>

    <!-- Header Actions (Desktop) -->
    <div class="header-actions desktop-actions">
      <NSpace align="center" size="small">
        <!-- System Stats (local box) - Desktop version with progress bars -->
        <NTooltip v-if="localStats && !localStats.error" :delay="300">
          <template #trigger>
            <div
              class="header-stats"
              :class="{
                'stats-warning': (localStats.cpu_percent ?? 0) >= 80 || (localStats.memory_percent ?? 0) >= 80,
                'stats-critical': isStatCritical(localStats.cpu_percent) || isStatCritical(localStats.memory_percent)
              }"
            >
              <div class="stat-mini" :class="{ critical: isStatCritical(localStats.cpu_percent) }">
                <NIcon size="12"><PhCpu weight="duotone" /></NIcon>
                <NProgress
                  type="line"
                  :percentage="localStats.cpu_percent ?? 0"
                  :show-indicator="false"
                  :height="4"
                  :border-radius="2"
                  :color="getStatColor(localStats.cpu_percent, '#3b82f6')"
                  style="width: 40px"
                />
                <span :style="{ color: getStatColor(localStats.cpu_percent, 'inherit') }">
                  {{ localStats.cpu_percent?.toFixed(0) }}%
                </span>
              </div>
              <div class="stat-mini" :class="{ critical: isStatCritical(localStats.memory_percent) }">
                <NIcon size="12"><PhMemory weight="duotone" /></NIcon>
                <NProgress
                  type="line"
                  :percentage="localStats.memory_percent ?? 0"
                  :show-indicator="false"
                  :height="4"
                  :border-radius="2"
                  :color="getStatColor(localStats.memory_percent, '#22c55e')"
                  style="width: 40px"
                />
                <span :style="{ color: getStatColor(localStats.memory_percent, 'inherit') }">
                  {{ localStats.memory_percent?.toFixed(0) }}%
                </span>
              </div>
            </div>
          </template>
          <div>
            <strong>{{ t('header.local_system') }}</strong><br>
            CPU: {{ localStats.cpu_percent?.toFixed(1) }}%<br>
            RAM: {{ ((localStats.memory_used_mb ?? 0) / 1024).toFixed(1) }}GB / {{ ((localStats.memory_total_mb ?? 0) / 1024).toFixed(1) }}GB
          </div>
        </NTooltip>

        <!-- Auth Indicator -->
        <NTooltip v-if="showAuthIndicator" :delay="300">
          <template #trigger>
            <div class="auth-indicator" :aria-label="authTooltip">
              <NIcon size="16" color="var(--success-color)">
                <PhLockKey weight="duotone" />
              </NIcon>
              <span class="auth-username">{{ authStore.displayUsername }}</span>
            </div>
          </template>
          {{ authTooltip }}
        </NTooltip>

        <!-- Snapshot Indicator -->
        <NTooltip :delay="300">
          <template #trigger>
            <div
              class="snapshot-dot"
              :style="snapshotDotStyle"
              :aria-label="snapshotTooltip"
            />
          </template>
          {{ snapshotTooltip }}
        </NTooltip>

        <NSelect
          v-model:value="currentLocale"
          :options="localeOptions"
          size="tiny"
          style="width: 90px"
          :consistent-menu-width="false"
        />
        <span class="text-muted mode-label" aria-live="polite">{{ themeLabel }}</span>
        <NButton
          quaternary
          circle
          size="small"
          @click="toggleTheme"
          :aria-label="t('header.theme_switch', { mode: appStore.isDark ? 'light' : 'dark' })"
          :title="t('header.toggle_theme')"
        >
          <NIcon size="18" aria-hidden="true">
            <component :is="themeIcon" weight="duotone" />
          </NIcon>
        </NButton>
        <CommandPalette />
        <ShortcutsOverlay />
      </NSpace>
    </div>

    <!-- Header Actions (Mobile) - ultra minimal stats -->
    <div class="header-actions mobile-actions">
      <span v-if="localStats && !localStats.error" class="mobile-stats">
        <span class="stat-label">CPU</span>
        <span class="stat-value" :style="{ color: getStatColorGreen(localStats.cpu_percent) }">{{ localStats.cpu_percent?.toFixed(0) }}</span>
        <span class="stat-label">MEM</span>
        <span class="stat-value" :style="{ color: getStatColorGreen(localStats.memory_percent) }">{{ localStats.memory_percent?.toFixed(0) }}</span>
      </span>
    </div>

    <!-- Mobile Navigation Drawer -->
    <NDrawer 
      v-model:show="isMobileMenuOpen" 
      placement="right" 
      :width="280"
      :trap-focus="false"
      :block-scroll="false"
    >
      <NDrawerContent :title="t('nav.navigation')" closable>
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
              <component :is="link.icon" weight="duotone" />
            </NIcon>
            <div class="mobile-nav-content">
              <span class="mobile-nav-label">{{ link.label }}</span>
              <span class="mobile-nav-shortcut text-muted">{{ link.shortcut }}</span>
            </div>
          </RouterLink>
          <div class="mobile-nav-footer">
            <NSelect
              v-model:value="currentLocale"
              :options="localeOptions"
              size="small"
              style="width: 100%"
            />
          </div>
        </nav>
      </NDrawerContent>
    </NDrawer>
  </header>

  <!-- Offline Banner -->
  <div v-if="!appStore.isOnline" class="offline-banner" role="alert" aria-live="assertive">
    <NIcon size="16"><PhWifiSlash weight="duotone" /></NIcon>
    <span>{{ t('a11y.offline_banner') }}</span>
  </div>
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
  background: var(--hover-overlay);
}

.nav-link:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.nav-link.active {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-bg);
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

/* Active Box Pill */
.active-box-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  background: var(--accent-bg);
  border: 1px solid var(--accent);
  white-space: nowrap;
  margin-left: 4px;
}

/* Header Actions */
.header-actions {
  display: flex;
  align-items: center;
}

/* Header Stats */
.header-stats {
  display: flex;
  gap: 12px;
  padding: 6px 12px;
  background: var(--hover-overlay);
  border: 1px solid var(--hover-overlay);
  border-radius: 8px;
  transition: all 0.3s ease;
}

.header-stats.stats-warning {
  border-color: rgba(249, 115, 22, 0.3);
  background: rgba(249, 115, 22, 0.05);
}

.header-stats.stats-critical {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.08);
  animation: stats-pulse 1.5s ease-in-out infinite;
}

@keyframes stats-pulse {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
  }
  50% {
    box-shadow: 0 0 8px 2px rgba(239, 68, 68, 0.3);
  }
}

.stat-mini {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
}

.stat-mini.critical {
  color: #ef4444;
}

.stat-mini span {
  font-family: 'JetBrains Mono', monospace;
  min-width: 28px;
  transition: color 0.3s ease;
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

.snapshot-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  transition: background 1s ease, box-shadow 1s ease;
  cursor: help;
  flex-shrink: 0;
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

/* Mobile header elements */
.mobile-actions {
  display: none;
}

.mobile-stats {
  display: flex;
  align-items: center;
  gap: 3px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: -0.3px;
}

.mobile-stats .stat-label {
  color: var(--accent);
  font-size: 8px;
}

.mobile-stats .stat-value {
  min-width: 16px;
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
  background: var(--hover-overlay);
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

.mobile-nav-footer {
  margin-top: 16px;
  padding: 16px 20px;
  border-top: 1px solid var(--divider-color);
}

/* Responsive Design */
@media (max-width: 768px) {
  .app-header {
    grid-template-columns: auto 1fr auto;
    padding: 0 6px;
    gap: 8px;
    min-height: 14px;
    height: 14px;
    background: var(--panel-bg-solid);
    border-bottom: 1px solid var(--hover-overlay);
  }

  .desktop-nav {
    display: none;
  }

  .desktop-actions {
    display: none;
  }

  .mobile-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
  }

  .brand-logo {
    height: 10px;
    border-radius: 2px;
  }

  .brand {
    display: flex;
    align-items: center;
  }
}

/* Offline Banner */
.offline-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 6px 16px;
  background: rgba(245, 158, 11, 0.15);
  border-bottom: 1px solid rgba(245, 158, 11, 0.3);
  color: #f59e0b;
  font-size: 13px;
  font-weight: 500;
  animation: slide-down 0.3s ease;
}

@keyframes slide-down {
  from { transform: translateY(-100%); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

/* Reduced Motion Support */
@media (prefers-reduced-motion: reduce) {
  .nav-link,
  .mobile-nav-link,
  .brand {
    transition: none;
  }
  .offline-banner {
    animation: none;
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
