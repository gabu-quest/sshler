<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { NIcon, NDrawer, NDrawerContent, NTooltip, NPopover } from "naive-ui";
import {
  PhCaretRight,
  PhChartBar,
  PhColumns,
  PhCpu,
  PhFolderSimple,
  PhGearSix,
  PhGitDiff,
  PhGlobe,
  PhHardDrives,
  PhHouseLine,
  PhList,
  PhLockKey,
  PhMemory,
  PhMoon,
  PhRobot,
  PhSidebarSimple,
  PhSquaresFour,
  PhSun,
  PhTerminal,
  PhToolbox,
  PhWifiSlash,
} from "@phosphor-icons/vue";

import { useAppStore } from "@/stores/app";
import { useAuthStore } from "@/stores/auth";
import { useProgressStore } from "@/stores/progress";
import { getEmojiForBox } from "@/utils/emoji-favicon";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useResponsive } from "@/composables/useResponsive";
import { useI18n, availableLocales } from "@/i18n";
import type { Locale } from "@/i18n";
import { boxStats } from "@/api/http";
import type { BoxStats } from "@/api/types";
import CommandPalette from "@/components/CommandPalette.vue";
import ProgressPicker from "@/components/ProgressPicker.vue";
import ShortcutsOverlay from "@/components/ShortcutsOverlay.vue";

const appStore = useAppStore();
const authStore = useAuthStore();
const bootstrapStore = useBootstrapStore();
const progressStore = useProgressStore();
const route = useRoute();
const router = useRouter();
const { t, locale, setLocale } = useI18n();

// One breakpoint, owned by useResponsive: below it is a phone (a bar on top and a
// menu drawer), from it on a desktop (the side rail). The width is read at once, so
// the first paint already has the right shell.
const { width, breakpoints } = useResponsive();
const isPhone = computed(() => width.value < breakpoints.tablet);

// The palette and shortcuts rows hold their own button in the icon cell. A click on
// the rest of the row (the label) presses that button, so the whole row opens it.
function openRailUtility(event: MouseEvent) {
  const row = event.currentTarget as HTMLElement;
  const button = row.querySelector<HTMLElement>(".rail-icon button");
  if (!button || button.contains(event.target as Node)) return;
  button.click();
}
// On a phone only the terminal keeps the 14 px bar; every other view has the 36 px
// bar with the menu button.
const thinBar = computed(() => route.path === "/terminal");

// System stats for local box
const localStats = ref<BoxStats | null>(null);
const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token);

async function loadLocalStats() {
  try {
    const stats = await boxStats('local', tokenValue.value || null);
    localStats.value = stats;
  } catch {
    // Silent on purpose: the rail has no room for a sentence, and this poll asks again
    // in ten seconds. Its widget is hidden while the stats fail, never left with old numbers.
    localStats.value = null;
  }
}
const statsShown = computed(() => localStats.value !== null && !localStats.value.error);

function statLevel(percent: number | null | undefined): "ok" | "warning" | "critical" {
  const p = percent ?? 0;
  if (p >= 90) return "critical";
  if (p >= 75) return "warning";
  return "ok";
}

function statScale(percent: number | null | undefined): string {
  return `scaleX(${Math.max(0, Math.min(100, percent ?? 0)) / 100})`;
}

let statsInterval: number | null = null;

const showAuthIndicator = computed(() => bootstrapStore.authRequired && authStore.isAuthenticated);
const authTooltip = computed(() => {
  if (!bootstrapStore.authRequired) return '';
  return authStore.isAuthenticated
    ? t('header.authenticated_as', { user: authStore.displayUsername })
    : t('header.auth_required');
});

const buildBoxQuery = () => {
  const box = appStore.activeBox;
  const dir = appStore.activeDir;
  return box
    ? `?box=${encodeURIComponent(box)}${dir ? `&dir=${encodeURIComponent(dir)}` : ''}`
    : '';
};

// The four daily links, the tools behind "Tools", and settings at the bottom.
// `allLinks` is the one table the Alt shortcuts and the shortcuts overlay read.
const primaryLinks = computed(() => {
  const boxQuery = buildBoxQuery();
  return [
    { to: "/", label: t("nav.overview"), icon: PhHouseLine, shortcut: "Alt+H" },
    { to: `/files${boxQuery}`, label: t("nav.files"), icon: PhFolderSimple, shortcut: "Alt+F" },
    { to: `/terminal${boxQuery}`, label: t("nav.terminal"), icon: PhTerminal, shortcut: "Alt+T" },
    { to: `/multi-terminal${boxQuery}`, label: t("nav.multi_terminal"), icon: PhSquaresFour, shortcut: "Alt+M" },
  ];
});

const toolLinks = computed(() => {
  const boxQuery = buildBoxQuery();
  return [
    { to: `/commander${boxQuery}`, label: t("commander.title"), icon: PhColumns, shortcut: "Alt+C" },
    { to: "/boxes", label: t("nav.boxes"), icon: PhHardDrives, shortcut: "Alt+B" },
    { to: "/claude", label: t("nav.claude"), icon: PhRobot, shortcut: "Alt+L" },
    { to: "/diff", label: t("nav.diff"), icon: PhGitDiff, shortcut: "Alt+D" },
    { to: "/progress", label: t("nav.progress"), icon: PhChartBar, shortcut: "Alt+P" },
    { to: "/artifacts", label: t("nav.artifacts"), icon: PhGlobe, shortcut: "Alt+A" },
  ];
});

const settingsLink = computed(() => (
  { to: "/settings", label: t("nav.settings"), icon: PhGearSix, shortcut: "Alt+S" }
));

const allLinks = computed(() => [...primaryLinks.value, ...toolLinks.value, settingsLink.value]);

const activeBoxEmoji = computed(() => appStore.activeBox ? getEmojiForBox(appStore.activeBox) : null);
const subscribedCount = computed(() => progressStore.subscribedBars.length);

const themeIcon = computed(() => (appStore.isDark ? PhMoon : PhSun));
const themeTitle = computed(() => {
  const resolved = appStore.isDark ? t("header.theme_dark") : t("header.theme_light");
  return appStore.colorMode === "system" ? t("header.theme_system", { resolved }) : resolved;
});
const themeSwitchLabel = computed(() =>
  t("header.theme_switch", { mode: appStore.isDark ? t("header.theme_light") : t("header.theme_dark") }),
);

const localeCode = computed(() => locale.value.toUpperCase());
const localeName = computed(() => availableLocales.find((l) => l.value === locale.value)?.label ?? localeCode.value);
function switchLocale() {
  const index = availableLocales.findIndex((l) => l.value === locale.value);
  const next = availableLocales[(index + 1) % availableLocales.length];
  if (next) setLocale(next.value as Locale);
}

const isActive = (path: string) => {
  const basePath = path.split('?')[0] ?? path;
  if (basePath === "/") {
    return route.path === "/";
  }
  return route.path.startsWith(basePath);
};
const toolsActive = computed(() => toolLinks.value.some((link) => isActive(link.to)));

const railEl = ref<HTMLElement | null>(null);
const toolsOpen = ref(false);
const progressPickerOpen = ref(false);
const toggleLabel = computed(() => (appStore.railCollapsed ? t("rail.expand") : t("rail.collapse")));

function closeRail() {
  if (!appStore.railCollapsed) appStore.toggleRailCollapsed();
}

// A pick closes whatever was open to make it: the expanded rail, the tools flyout,
// the phone drawer.
function onPick() {
  toolsOpen.value = false;
  appStore.mobileNavOpen = false;
  closeRail();
}

watch(() => route.path, onPick);

watch(isPhone, (phone) => {
  if (!phone) appStore.mobileNavOpen = false;
  toolsOpen.value = false;
});

// The expanded rail floats over the page; a press anywhere but on it (or on its tools
// flyout, which is drawn outside it) closes it.
function onPointerDown(event: PointerEvent) {
  if (appStore.railCollapsed || isPhone.value) return;
  const target = event.target;
  if (target instanceof Element && (railEl.value?.contains(target) || target.closest(".rail-flyout"))) return;
  closeRail();
}

// Don't hijack shortcuts while the user is typing (input/textarea/contenteditable,
// including xterm.js's internal textarea): a key meant for the field stays there.
function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

// `allLinks` is the single source of truth for the Alt+letter table: the letter is
// derived from each link's `shortcut` ("Alt+H" -> "h"), so the keys can never drift
// from what the rail, the drawer and ShortcutsOverlay show. Alt+\ toggles the rail.
const handleKeydown = (event: KeyboardEvent) => {
  if (isTypingTarget(event.target)) return;
  if (event.key === "Escape") {
    if (!isPhone.value && !appStore.railCollapsed) closeRail();
    return;
  }
  if (!event.altKey) return;
  if (event.code === "Backslash" || event.key === "\\") {
    if (isPhone.value) return;
    event.preventDefault();
    appStore.toggleRailCollapsed();
    return;
  }
  const key = event.key.toLowerCase();
  const link = allLinks.value.find((l) => l.shortcut.toLowerCase() === `alt+${key}`);
  if (link) {
    event.preventDefault();
    router.push(link.to);
  }
};

// Only poll stats when the tab is visible (saves requests when 20+ tabs open)
const startStatsPolling = () => {
  // Nothing is asked of the server before the page knows it is signed in.
  if (statsInterval || !bootstrapStore.signedIn) return;
  loadLocalStats();
  statsInterval = window.setInterval(loadLocalStats, 10000);
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

// The poll runs only while the page is signed in: it starts when it becomes so and
// stops when it is signed out.
watch(() => bootstrapStore.signedIn, (signedIn) => {
  if (signedIn) {
    if (!document.hidden) startStatsPolling();
  } else {
    stopStatsPolling();
  }
});

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('visibilitychange', handleVisibilityChange);
  if (!document.hidden) {
    startStatsPolling();
  }
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
  document.removeEventListener('pointerdown', onPointerDown, true);
  document.removeEventListener('visibilitychange', handleVisibilityChange);
  stopStatsPolling();
});
</script>

<template>
  <!-- Desktop: the side rail. It always takes the collapsed width in the page's row;
       expanded, its panel floats over the page, so the page never changes width. -->
  <aside
    v-if="!isPhone"
    ref="railEl"
    class="app-rail"
    :class="{ expanded: !appStore.railCollapsed }"
    :aria-label="t('nav.navigation')"
  >
    <div class="rail-shadow" aria-hidden="true" />
    <div class="rail-panel">
      <div class="rail-inner">
        <div class="rail-top">
          <RouterLink class="rail-item rail-brand" to="/" :aria-label="t('nav.overview')" @click="onPick">
            <span class="rail-icon"><img src="/logo.png" alt="" class="brand-logo" /></span>
            <span class="rail-label rail-brand-name">sshler</span>
          </RouterLink>
          <button
            type="button"
            class="rail-item rail-toggle"
            data-testid="rail-toggle"
            :aria-expanded="!appStore.railCollapsed"
            :aria-label="toggleLabel"
            :title="`${toggleLabel} (Alt+\\)`"
            @click="appStore.toggleRailCollapsed()"
          >
            <span class="rail-icon">
              <NIcon size="18" aria-hidden="true"><PhSidebarSimple weight="duotone" /></NIcon>
            </span>
            <span class="rail-label">{{ toggleLabel }}</span>
          </button>
        </div>

        <nav class="nav desktop-nav" :aria-label="t('nav.navigation')">
          <RouterLink
            v-for="link in primaryLinks"
            :key="link.to"
            :to="link.to"
            class="rail-item"
            :class="{ active: isActive(link.to) }"
            :aria-label="`${link.label} (${link.shortcut})`"
            :title="`${link.label} - ${link.shortcut}`"
            @click="onPick"
          >
            <span class="rail-icon">
              <NIcon size="18" aria-hidden="true"><component :is="link.icon" weight="duotone" /></NIcon>
            </span>
            <span class="rail-label">{{ link.label }}</span>
            <span class="rail-hint">{{ link.shortcut }}</span>
          </RouterLink>

          <NPopover
            trigger="click"
            placement="right-start"
            :show="toolsOpen"
            :show-arrow="false"
            class="rail-flyout"
            @update:show="(value: boolean) => (toolsOpen = value)"
          >
            <template #trigger>
              <button
                type="button"
                class="rail-item"
                :class="{ active: toolsActive }"
                data-testid="rail-tools"
                aria-haspopup="menu"
                :aria-expanded="toolsOpen"
                :aria-label="t('nav.tools')"
                :title="t('nav.tools')"
              >
                <span class="rail-icon">
                  <NIcon size="18" aria-hidden="true"><PhToolbox weight="duotone" /></NIcon>
                </span>
                <span class="rail-label">{{ t('nav.tools') }}</span>
                <span class="rail-hint">
                  <NIcon size="12" aria-hidden="true"><PhCaretRight weight="bold" /></NIcon>
                </span>
              </button>
            </template>
            <div class="rail-tools-menu" data-testid="rail-tools-menu" role="menu">
              <RouterLink
                v-for="link in toolLinks"
                :key="link.to"
                :to="link.to"
                class="rail-item rail-tools-item"
                :class="{ active: isActive(link.to) }"
                role="menuitem"
                :aria-label="`${link.label} (${link.shortcut})`"
                @click="onPick"
              >
                <span class="rail-icon">
                  <NIcon size="18" aria-hidden="true"><component :is="link.icon" weight="duotone" /></NIcon>
                </span>
                <span class="rail-label">{{ link.label }}</span>
                <span class="rail-hint">{{ link.shortcut }}</span>
              </RouterLink>
            </div>
          </NPopover>
        </nav>

        <div v-if="appStore.activeBox" class="rail-context">
          <div class="rail-item rail-box" :title="t('nav.active_box', { box: appStore.activeBox })">
            <span class="rail-icon rail-box-emoji" aria-hidden="true">{{ activeBoxEmoji }}</span>
            <span class="rail-label">{{ appStore.activeBox }}</span>
          </div>
          <button
            type="button"
            class="rail-item"
            data-testid="rail-progress"
            :aria-label="t('rail.progress')"
            :title="t('progress.strip.manage', { box: appStore.activeBox })"
            @click="progressPickerOpen = true"
          >
            <span class="rail-icon">
              <NIcon size="18" aria-hidden="true"><PhChartBar weight="duotone" /></NIcon>
              <span v-if="subscribedCount > 0" class="rail-badge" data-testid="rail-progress-count">{{ subscribedCount }}</span>
            </span>
            <span class="rail-label">{{ t('rail.progress') }}</span>
          </button>
        </div>

        <div class="rail-dock">
          <NTooltip v-if="statsShown && localStats" placement="right" :delay="300">
            <template #trigger>
              <div class="rail-stats" data-testid="local-stats">
                <div class="rail-stat" :class="`rail-stat--${statLevel(localStats.cpu_percent)}`">
                  <span class="rail-icon rail-stat-cell">
                    <span class="rail-stat-key">CPU</span>
                    <span class="rail-stat-num">{{ localStats.cpu_percent?.toFixed(0) }}</span>
                  </span>
                  <span class="rail-label rail-stat-detail">
                    <NIcon size="12" aria-hidden="true"><PhCpu weight="duotone" /></NIcon>
                    <span class="rail-stat-track"><span class="rail-stat-fill" :style="{ transform: statScale(localStats.cpu_percent) }" /></span>
                    <span class="rail-stat-pct">{{ localStats.cpu_percent?.toFixed(0) }}%</span>
                  </span>
                </div>
                <div class="rail-stat" :class="`rail-stat--${statLevel(localStats.memory_percent)}`">
                  <span class="rail-icon rail-stat-cell">
                    <span class="rail-stat-key">MEM</span>
                    <span class="rail-stat-num">{{ localStats.memory_percent?.toFixed(0) }}</span>
                  </span>
                  <span class="rail-label rail-stat-detail">
                    <NIcon size="12" aria-hidden="true"><PhMemory weight="duotone" /></NIcon>
                    <span class="rail-stat-track"><span class="rail-stat-fill" :style="{ transform: statScale(localStats.memory_percent) }" /></span>
                    <span class="rail-stat-pct">{{ localStats.memory_percent?.toFixed(0) }}%</span>
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

          <div v-if="showAuthIndicator" class="rail-item rail-auth" :title="authTooltip" :aria-label="authTooltip">
            <span class="rail-icon">
              <NIcon size="16" aria-hidden="true"><PhLockKey weight="duotone" /></NIcon>
            </span>
            <span class="rail-label">{{ authStore.displayUsername }}</span>
          </div>

          <div class="rail-item rail-utility" @click="openRailUtility">
            <span class="rail-icon"><CommandPalette /></span>
            <span class="rail-label">{{ t('palette.title') }}</span>
          </div>
          <div class="rail-item rail-utility" @click="openRailUtility">
            <span class="rail-icon"><ShortcutsOverlay :links="allLinks" /></span>
            <span class="rail-label">{{ t('shortcuts.title') }}</span>
          </div>
          <button
            type="button"
            class="rail-item"
            data-testid="rail-theme"
            :title="themeTitle"
            :aria-label="themeSwitchLabel"
            @click="appStore.toggleTheme()"
          >
            <span class="rail-icon">
              <NIcon size="18" aria-hidden="true"><component :is="themeIcon" weight="duotone" /></NIcon>
            </span>
            <span class="rail-label">{{ themeTitle }}</span>
          </button>
          <button
            type="button"
            class="rail-item"
            data-testid="rail-locale"
            :title="t('header.switch_language')"
            :aria-label="t('header.switch_language')"
            @click="switchLocale"
          >
            <span class="rail-icon rail-locale-code">{{ localeCode }}</span>
            <span class="rail-label">{{ localeName }}</span>
          </button>
          <RouterLink
            :to="settingsLink.to"
            class="rail-item"
            :class="{ active: isActive(settingsLink.to) }"
            :aria-label="`${settingsLink.label} (${settingsLink.shortcut})`"
            :title="`${settingsLink.label} - ${settingsLink.shortcut}`"
            @click="onPick"
          >
            <span class="rail-icon">
              <NIcon size="18" aria-hidden="true"><PhGearSix weight="duotone" /></NIcon>
            </span>
            <span class="rail-label">{{ settingsLink.label }}</span>
            <span class="rail-hint">{{ settingsLink.shortcut }}</span>
          </RouterLink>
        </div>
      </div>
    </div>
  </aside>

  <!-- Phone: the terminal's 14 px bar, or the 36 px bar with the menu button. -->
  <header v-else class="app-header" :class="{ 'app-header--nav': !thinBar }" role="banner">
    <button
      v-if="!thinBar"
      type="button"
      class="mobile-menu-btn"
      :aria-label="t('header.menu')"
      :aria-expanded="appStore.mobileNavOpen"
      @click="appStore.mobileNavOpen = true"
    >
      <NIcon size="20" aria-hidden="true"><PhList weight="bold" /></NIcon>
    </button>
    <RouterLink class="brand" to="/" :aria-label="t('nav.overview')">
      <img src="/logo.png" alt="sshler" class="brand-logo" />
    </RouterLink>
    <span
      v-if="!thinBar && appStore.activeBox"
      class="active-box-pill"
      :title="t('nav.active_box', { box: appStore.activeBox })"
    >
      {{ activeBoxEmoji }} {{ appStore.activeBox }}
    </span>
    <span class="header-spacer" />
    <span v-if="statsShown && localStats" class="mobile-stats" data-testid="local-stats">
      <span class="stat-label">CPU</span>
      <span class="stat-value" :class="`stat-value--${statLevel(localStats.cpu_percent)}`">{{ localStats.cpu_percent?.toFixed(0) }}</span>
      <span class="stat-label">MEM</span>
      <span class="stat-value" :class="`stat-value--${statLevel(localStats.memory_percent)}`">{{ localStats.memory_percent?.toFixed(0) }}</span>
    </span>
  </header>

  <!-- The phone's menu drawer, from the left like the desktop rail. -->
  <NDrawer
    v-if="isPhone"
    v-model:show="appStore.mobileNavOpen"
    placement="left"
    :width="280"
    :trap-focus="false"
    :block-scroll="false"
  >
    <NDrawerContent :title="t('nav.navigation')" closable>
      <nav class="mobile-nav" :aria-label="t('nav.navigation')">
        <template v-for="(group, index) in [primaryLinks, toolLinks, [settingsLink]]" :key="index">
          <div v-if="index === 1" class="mobile-nav-heading">{{ t('nav.tools') }}</div>
          <div v-if="index === 2" class="mobile-nav-divider" />
          <RouterLink
            v-for="link in group"
            :key="link.to"
            :to="link.to"
            class="mobile-nav-link"
            :class="{ active: isActive(link.to) }"
            :aria-label="link.label"
            @click="onPick"
          >
            <NIcon size="20" aria-hidden="true">
              <component :is="link.icon" weight="duotone" />
            </NIcon>
            <span class="mobile-nav-content">
              <span class="mobile-nav-label">{{ link.label }}</span>
              <span class="mobile-nav-shortcut">{{ link.shortcut }}</span>
            </span>
          </RouterLink>
        </template>
      </nav>
      <div class="mobile-nav-footer">
        <span
          v-if="appStore.activeBox"
          class="active-box-pill"
          :title="t('nav.active_box', { box: appStore.activeBox })"
        >
          {{ activeBoxEmoji }} {{ appStore.activeBox }}
        </span>
        <div class="mobile-nav-actions">
          <button
            v-if="appStore.activeBox"
            type="button"
            class="mobile-nav-action"
            :aria-label="t('rail.progress')"
            :title="t('rail.progress')"
            @click="progressPickerOpen = true"
          >
            <NIcon size="18" aria-hidden="true"><PhChartBar weight="duotone" /></NIcon>
            <span v-if="subscribedCount > 0" class="rail-badge">{{ subscribedCount }}</span>
          </button>
          <button
            type="button"
            class="mobile-nav-action"
            :title="themeTitle"
            :aria-label="themeSwitchLabel"
            @click="appStore.toggleTheme()"
          >
            <NIcon size="18" aria-hidden="true"><component :is="themeIcon" weight="duotone" /></NIcon>
          </button>
          <button
            type="button"
            class="mobile-nav-action mobile-nav-locale"
            :title="t('header.switch_language')"
            :aria-label="t('header.switch_language')"
            @click="switchLocale"
          >
            {{ localeCode }}
          </button>
          <CommandPalette />
          <ShortcutsOverlay :links="allLinks" />
        </div>
        <div v-if="showAuthIndicator" class="mobile-nav-auth" :title="authTooltip">
          <NIcon size="16" aria-hidden="true"><PhLockKey weight="duotone" /></NIcon>
          <span>{{ authStore.displayUsername }}</span>
        </div>
      </div>
    </NDrawerContent>
  </NDrawer>

  <ProgressPicker v-if="appStore.activeBox" v-model:show="progressPickerOpen" />

  <!-- Offline Banner -->
  <Transition name="slide-down-sm">
    <div v-if="!appStore.isOnline" class="offline-banner" role="alert" aria-live="assertive">
      <NIcon size="16"><PhWifiSlash weight="duotone" /></NIcon>
      <span>{{ t('a11y.offline_banner') }}</span>
    </div>
  </Transition>
</template>

<style scoped>
/* ---------- The desktop side rail ---------- */

/* The rail's place in the page's row: always the collapsed width, the window's height. */
.app-rail {
  position: relative;
  flex: 0 0 var(--rail-width-collapsed);
  width: var(--rail-width-collapsed);
  height: 100%;
  z-index: 200;
}

/* The panel is always the expanded width. Collapsed, it is slid left so only its first
   52 px show; its content is slid right by the same amount, so the icons stand still
   while the panel's edge moves. Both move by transform only. */
.rail-panel,
.rail-shadow {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: var(--rail-width-expanded);
  transform: translateX(calc(var(--rail-width-collapsed) - var(--rail-width-expanded)));
  transition: transform var(--dur-base) var(--ease-out);
}

.rail-panel {
  overflow: clip;
  background: var(--surface);
  border-right: 1px solid var(--stroke);
}

.rail-shadow {
  box-shadow: 6px 0 28px var(--shadow-lg);
  opacity: 0;
  pointer-events: none;
  transition: transform var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out);
}

.rail-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 6px 0;
  box-sizing: border-box;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-width: none;
  transform: translateX(calc(var(--rail-width-expanded) - var(--rail-width-collapsed)));
  transition: transform var(--dur-base) var(--ease-out);
}

.rail-inner::-webkit-scrollbar {
  display: none;
}

.app-rail.expanded .rail-panel,
.app-rail.expanded .rail-inner,
.app-rail.expanded .rail-shadow {
  transform: translateX(0);
}

.app-rail.expanded .rail-shadow {
  opacity: 1;
}

.rail-top,
.rail-context {
  display: flex;
  flex-direction: column;
  padding-bottom: 6px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--stroke);
}

.rail-context {
  margin-top: 6px;
  border-top: 1px solid var(--stroke);
  padding-top: 6px;
}

.desktop-nav {
  display: flex;
  flex-direction: column;
}

.rail-dock {
  display: flex;
  flex-direction: column;
  margin-top: auto;
  padding-top: 6px;
  border-top: 1px solid var(--stroke);
}

/* One row of the rail: a 52 px icon cell, then the label and its shortcut hint (seen
   only when the rail is expanded). */
.rail-item {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  width: 100%;
  height: 36px;
  padding: 0;
  margin: 0;
  border: none;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  text-align: left;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  position: relative;
  transition: color var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out);
}

.rail-icon {
  flex: 0 0 var(--rail-width-collapsed);
  width: var(--rail-width-collapsed);
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  border-radius: var(--radius-md);
  transition: transform var(--dur-instant) var(--ease-out), background-color var(--dur-fast) var(--ease-out);
}

.rail-label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rail-hint {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  padding: 0 10px 0 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--muted);
  opacity: 0.75;
}

/* Collapsed, the highlight is the icon cell; expanded, the whole row. */
.app-rail:not(.expanded) .rail-icon {
  width: 40px;
  flex-basis: 40px;
  margin: 0 6px;
}

.rail-item:hover {
  color: var(--text);
}

.app-rail:not(.expanded) .rail-item:hover .rail-icon,
.app-rail.expanded .rail-item:hover,
.rail-tools-item:hover {
  background: var(--hover-overlay);
}

.rail-item:active .rail-icon {
  transform: scale(0.92);
}

.rail-item:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.rail-item.active {
  color: var(--accent);
}

.app-rail:not(.expanded) .rail-item.active .rail-icon,
.app-rail.expanded .rail-item.active,
.rail-tools-item.active {
  background: var(--accent-bg);
}

.rail-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 9px;
  bottom: 9px;
  width: 2px;
  border-radius: 1px;
  background: var(--accent);
}

.rail-brand-name {
  font-weight: 600;
  color: var(--text);
  letter-spacing: 0.2px;
}

.brand-logo {
  height: 22px;
  width: auto;
  object-fit: contain;
  border-radius: 4px;
}

/* In the rail's icon cell only the logo's mark shows: the word is the label beside it. */
.rail-brand .brand-logo {
  width: 22px;
  object-fit: cover;
  object-position: left center;
}

.rail-box {
  cursor: default;
  color: var(--text);
  font-weight: 500;
}

.rail-box-emoji {
  font-size: 18px;
}

.rail-badge {
  position: absolute;
  top: 3px;
  right: 3px;
  min-width: 14px;
  height: 14px;
  padding: 0 3px;
  box-sizing: border-box;
  border-radius: 7px;
  background: var(--accent);
  color: var(--surface);
  font-size: 9px;
  font-weight: 700;
  line-height: 14px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.rail-utility {
  cursor: pointer;
}

.rail-locale-code {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.rail-auth {
  cursor: default;
  color: var(--success);
}

/* CPU and memory: a two-line readout in the icon cell, a bar and percent beside it. */
.rail-stats {
  display: flex;
  flex-direction: column;
  padding: 2px 0;
}

.rail-stat {
  display: flex;
  align-items: center;
  height: 26px;
  color: var(--muted);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.rail-stat-cell {
  height: 26px;
  flex-direction: column;
  gap: 0;
  line-height: 1;
}

.rail-stat-key {
  font-size: 8px;
  letter-spacing: 0.4px;
  color: var(--accent);
}

.rail-stat-num {
  font-size: 11px;
  font-weight: 600;
}

.rail-stat-detail {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding-right: 12px;
  font-size: 11px;
}

.rail-stat-track {
  flex: 1 1 auto;
  height: 4px;
  border-radius: 2px;
  background: var(--hover-overlay);
  overflow: hidden;
}

.rail-stat-fill {
  display: block;
  width: 100%;
  height: 100%;
  background: var(--success);
  transform-origin: left center;
  transition: transform var(--dur-slow) var(--ease-in-out);
}

.rail-stat--warning .rail-stat-num,
.rail-stat--warning .rail-stat-pct { color: var(--warning); }
.rail-stat--warning .rail-stat-fill { background: var(--warning); }
.rail-stat--critical .rail-stat-num,
.rail-stat--critical .rail-stat-pct { color: var(--error); }
.rail-stat--critical .rail-stat-fill { background: var(--error); }

/* The tools flyout (drawn by the popover, outside the rail). */
.rail-tools-menu {
  display: flex;
  flex-direction: column;
  min-width: 200px;
  margin: -4px -6px;
}

.rail-tools-item {
  border-radius: var(--radius-md);
}

.rail-tools-item .rail-icon {
  flex-basis: 40px;
  width: 40px;
}

/* ---------- The phone bar ---------- */

.app-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 6px;
  height: var(--header-height-mobile);
  min-height: var(--header-height-mobile);
  box-sizing: border-box;
  background: var(--panel-bg-solid);
  border-bottom: 1px solid var(--hover-overlay);
  position: relative;
  z-index: 100;
}

.app-header .brand {
  display: flex;
  align-items: center;
  text-decoration: none;
}

.app-header .brand-logo {
  height: 10px;
  border-radius: 2px;
}

.app-header--nav {
  height: var(--header-height-mobile-nav);
  min-height: var(--header-height-mobile-nav);
  padding: 0 8px 0 2px;
  border-bottom-color: var(--stroke);
}

.app-header--nav .brand-logo {
  height: 20px;
  border-radius: 4px;
}

.header-spacer {
  flex: 1 1 auto;
}

.mobile-menu-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), transform var(--dur-instant) var(--ease-out);
}

.mobile-menu-btn:hover {
  background: var(--hover-overlay);
}

.mobile-menu-btn:active {
  transform: scale(0.92);
}

.active-box-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  background: var(--accent-bg);
  border: 1px solid var(--accent);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 45vw;
}

.mobile-stats {
  display: flex;
  align-items: center;
  gap: 3px;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: -0.3px;
}

.app-header--nav .mobile-stats {
  font-size: 11px;
  gap: 4px;
}

.mobile-stats .stat-label {
  color: var(--accent);
  font-size: 8px;
}

.app-header--nav .mobile-stats .stat-label {
  font-size: 9px;
}

.mobile-stats .stat-value {
  min-width: 16px;
}

.stat-value--ok { color: var(--success); }
.stat-value--warning { color: var(--warning); }
.stat-value--critical { color: var(--error); }

/* ---------- The phone's menu drawer ---------- */

.mobile-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 0;
}

.mobile-nav-heading {
  margin: 12px 0 4px;
  padding: 0 16px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: var(--muted);
}

.mobile-nav-divider {
  margin: 8px 0;
  border-top: 1px solid var(--stroke);
}

.mobile-nav-link {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 16px;
  min-height: 44px; /* Touch target size */
  box-sizing: border-box;
  border-radius: var(--radius-md);
  border-left: 2px solid transparent;
  color: var(--muted);
  text-decoration: none;
  transition: color var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out);
}

.mobile-nav-link:hover,
.mobile-nav-link:focus-visible {
  color: var(--text);
  background: var(--hover-overlay);
}

.mobile-nav-link.active {
  color: var(--text);
  background: var(--accent-bg);
  border-left-color: var(--accent);
}

.mobile-nav-content {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.mobile-nav-label {
  font-weight: 500;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mobile-nav-shortcut {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted);
  opacity: 0.7;
}

.mobile-nav-footer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
  padding: 12px 16px 4px;
  border-top: 1px solid var(--stroke);
}

.mobile-nav-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.mobile-nav-action {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 36px;
  height: 36px;
  padding: 0 6px;
  border: 1px solid var(--stroke);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), transform var(--dur-instant) var(--ease-out);
}

.mobile-nav-action:active {
  transform: scale(0.94);
}

.mobile-nav-locale {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
}

.mobile-nav-auth {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--success);
}

/* ---------- Offline banner: over the page, at the top ---------- */
.offline-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 300;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 6px 16px;
  background: var(--warning-bg);
  border-bottom: 1px solid var(--warning);
  color: var(--warning);
  font-size: 13px;
  font-weight: 500;
}

/* High Contrast Support */
@media (prefers-contrast: high) {
  .rail-item.active {
    outline: 1px solid var(--text);
  }
}
</style>
