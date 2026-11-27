<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";

import {
  NButton,
  NCard,
  NGrid,
  NGridItem,
  NIcon,
  NModal,
  NInput,
  NSpin,
  NTag,
  NEmpty,
  NProgress
} from "naive-ui";
import { useI18n } from "@/i18n";
import {
  PhPlus,
  PhTerminal,
  PhFolderOpen,
  PhCircle,
  PhChartBar,
  PhStar,
  PhCpu,
  PhMemory,
  PhDotsThreeVertical
} from "@phosphor-icons/vue";

import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useSessionsStore } from "@/stores/sessions";
import { useFavoritesStore } from "@/stores/favorites";
import { boxStats, recreateSession, statsStreamUrl } from "@/api/http";
import { failureText, errorText } from "@/api/apiError";
import type { ApiSession, BoxStats } from "@/api/types";
import BoxSearchInput from "@/components/BoxSearchInput.vue";
import { filesUrl, openInBackgroundTab, terminalUrl } from "@/utils/dirLinks";
import { resetFavicon, getEmojiForString, getEmojiForBox } from "@/utils/emoji-favicon";

const router = useRouter();
const { t } = useI18n();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const sessionsStore = useSessionsStore();
const favoritesStore = useFavoritesStore();

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token);
const hasServers = computed(() => boxesStore.items.length > 0);
const pinnedBoxes = computed(() => boxesStore.items.filter(box => box.pinned));
const totalFavorites = computed(() =>
  boxesStore.items.reduce((acc, box) => acc + (box.favorites?.length || 0), 0)
);

// System stats per box
const statsMap = ref<Map<string, BoxStats>>(new Map());
// Why a box's stats could not be read: shown on its card in place of its numbers, and
// replaced by the next answer, so a failure that repeats stays one line.
const statsFailure = ref<Map<string, string>>(new Map());
const statsLoading = ref<Set<string>>(new Set());
const lastUpdated = ref<Date | null>(null);
let refreshInterval: ReturnType<typeof setInterval> | null = null;
let statsPollInterval: ReturnType<typeof setInterval> | null = null;
let statsEventSource: EventSource | null = null;

function startStatsStream(boxNames: string[]) {
  stopStatsStream()
  const url = statsStreamUrl(boxNames, tokenValue.value || null)
  statsEventSource = new EventSource(url)

  // Mark all boxes as loading until first event
  boxNames.forEach(n => statsLoading.value.add(n))

  statsEventSource.addEventListener('stats', (event: MessageEvent) => {
    try {
      const stats: BoxStats = JSON.parse(event.data)
      if (stats.name) {
        recordStats(stats.name, stats)
        statsLoading.value.delete(stats.name)
        lastUpdated.value = new Date()
      }
    } catch { /* ignore malformed events */ }
  })

  statsEventSource.onerror = () => {
    // SSE failed — fall back to polling. The stream's wait for a first event is over, so
    // the poll's own requests are not skipped as "already loading".
    stopStatsStream()
    boxNames.forEach(n => statsLoading.value.delete(n))
    startPollingFallback(boxNames)
  }
}

function stopStatsStream() {
  if (statsEventSource) {
    statsEventSource.close()
    statsEventSource = null
  }
}

/** A box's stats answer: its numbers, or the text of why its stats failed. */
function recordStats(boxName: string, stats: BoxStats) {
  if (stats.error != null || stats.reason != null) {
    statsMap.value.delete(boxName)
    statsFailure.value.set(boxName, failureText(stats))
    return
  }
  statsFailure.value.delete(boxName)
  statsMap.value.set(boxName, stats)
}

// Polling fallback if SSE fails
async function loadBoxStats(boxName: string) {
  if (statsLoading.value.has(boxName)) return;
  statsLoading.value.add(boxName);
  try {
    recordStats(boxName, await boxStats(boxName, tokenValue.value || null));
  } catch (err) {
    statsMap.value.delete(boxName);
    statsFailure.value.set(boxName, errorText(err));
  } finally {
    statsLoading.value.delete(boxName);
  }
}

function startPollingFallback(boxNames: string[]) {
  if (statsPollInterval) return  // Already polling
  boxNames.forEach(n => loadBoxStats(n))
  statsPollInterval = setInterval(() => {
    boxNames.forEach(n => loadBoxStats(n))
  }, 30_000)
}

function getBoxStats(boxName: string): BoxStats | undefined {
  return statsMap.value.get(boxName);
}

function formatUptime(seconds: number | null | undefined): string {
  if (!seconds) return '';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const mins = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function formatMemory(mb: number | null | undefined): string {
  if (!mb) return '';
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)}GB`;
  return `${Math.round(mb)}MB`;
}

// Favorites grouped by box for display
const favoritesByBox = computed(() => {
  const groups: Array<{ box: string; items: Array<{ box: string; path: string; label: string }> }> = []
  boxesStore.items.forEach(box => {
    const seen = new Set<string>()
    const items: Array<{ box: string; path: string; label: string }> = []
    // Get favorites from box data
    const boxFavs = box.favorites || []
    boxFavs.forEach(path => {
      seen.add(path)
      const label = path.split(/[/\\]/).filter(Boolean).pop() || path
      items.push({ box: box.name, path, label })
    })
    // Also get from favorites store (may have additional ones)
    const storeFavs = Array.from(favoritesStore.favoritesForBox(box.name).values())
    storeFavs.forEach(path => {
      if (!seen.has(path)) {
        const label = path.split(/[/\\]/).filter(Boolean).pop() || path
        items.push({ box: box.name, path, label })
      }
    })
    if (items.length > 0) {
      groups.push({ box: box.name, items })
    }
  })
  return groups
});

// Flat list for total count
const allFavorites = computed(() => favoritesByBox.value.flatMap(g => g.items));

// Favorite notes (localStorage-based)
const showFavSettings = ref(false)
const editingFavorite = ref<{ box: string; path: string; label: string } | null>(null)
const favNoteText = ref('')

const favNoteKey = (box: string, path: string) => `sshler-fav-note:${box}:${path}`

const getFavNote = (box: string, path: string): string => {
  return localStorage.getItem(favNoteKey(box, path)) || ''
}

const openFavSettings = (fav: { box: string; path: string; label: string }) => {
  editingFavorite.value = fav
  favNoteText.value = getFavNote(fav.box, fav.path)
  showFavSettings.value = true
}

const saveFavNote = () => {
  if (!editingFavorite.value) return
  const key = favNoteKey(editingFavorite.value.box, editingFavorite.value.path)
  if (favNoteText.value.trim()) {
    localStorage.setItem(key, favNoteText.value.trim())
  } else {
    localStorage.removeItem(key)
  }
  showFavSettings.value = false
  editingFavorite.value = null
}

// Real stats only - no fake data
const quickStats = computed(() => ({
  servers: boxesStore.items.length,
  pinnedBoxes: pinnedBoxes.value.length,
  favorites: totalFavorites.value
}));

onMounted(async () => {
  // Reset to default favicon on overview page
  document.title = 'sshler';
  resetFavicon();

  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null);
  }
  // Start SSE stats stream — replaces polling, updates progressively
  if (boxesStore.items.length > 0) {
    const boxNames = boxesStore.items.map(b => b.name)
    startStatsStream(boxNames)

    // Session sync stays as separate API call (lightweight, less frequent)
    sessionsStore.syncAllBoxSessions(boxNames, tokenValue.value || null)
      .then(() => { lastUpdated.value = new Date(); });

    // Refresh sessions every 30s (stats handled by SSE)
    refreshInterval = setInterval(() => {
      sessionsStore.syncAllBoxSessions(boxNames, tokenValue.value || null)
        .then(() => { lastUpdated.value = new Date(); });
    }, 30_000);
  }
});

onUnmounted(() => {
  stopStatsStream()
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }
  if (statsPollInterval) {
    clearInterval(statsPollInterval);
    statsPollInterval = null;
  }
});

// Get session info for a box
const getBoxSessionCount = (boxName: string): number => {
  return sessionsStore.getActiveSessionCount(boxName);
};

const getBoxLastUsed = (boxName: string): string => {
  const session = sessionsStore.getMostRecentSession(boxName);
  if (!session) return t('overview.never');
  return sessionsStore.formatRelativeTime(session.last_accessed_at);
};

const getBoxSessions = (boxName: string) => {
  return sessionsStore.getBoxSessions(boxName);
};

// Why a "Restore layout" failed, by session id: shown on its row until the next press.
const restoreFailure = ref<Map<string, string>>(new Map());

/** "Restore layout": the session is recreated from its last layout, then the box's
 * sessions are synced, so the row shows running. */
async function restoreLayout(boxName: string, session: ApiSession): Promise<void> {
  restoreFailure.value.delete(session.id);
  try {
    await recreateSession(session.id, tokenValue.value || null);
  } catch (err) {
    restoreFailure.value.set(session.id, errorText(err));
    return;
  }
  await sessionsStore.syncBoxSessions(boxName, tokenValue.value || null);
}

/** The one way from a card to a terminal: every part of the query is encoded. */
const openTerminal = (boxName: string, target: { session?: string; dir?: string } = {}) => {
  let query = `box=${encodeURIComponent(boxName)}`;
  if (target.session !== undefined) query += `&session=${encodeURIComponent(target.session)}`;
  if (target.dir !== undefined) query += `&dir=${encodeURIComponent(target.dir)}`;
  router.push(`/terminal?${query}`);
};

const getServerStatus = (box: any) => {
  const stats = getBoxStats(box.name);
  if (stats && stats.cpu_percent !== null) return 'online';
  if (statsLoading.value.has(box.name)) return 'online';
  return 'unknown';
};

const handleConnectToServer = (serverName: string) => {
  openTerminal(serverName);
};

const handleBrowseServerFiles = (serverName: string) => {
  router.push(`/files?box=${encodeURIComponent(serverName)}`);
};

const handleMultiTerminal = (serverName: string) => {
  router.push(`/multi-terminal?box=${encodeURIComponent(serverName)}`);
};

// Session chips on a card: four at first, the rest on request, per box.
const CHIP_LIMIT = 4;
const expandedBoxes = ref<Set<string>>(new Set());
const visibleSessions = (boxName: string): ApiSession[] => {
  const all = getBoxSessions(boxName);
  return expandedBoxes.value.has(boxName) ? all : all.slice(0, CHIP_LIMIT);
};
const toggleSessions = (boxName: string) => {
  const next = new Set(expandedBoxes.value);
  if (next.has(boxName)) next.delete(boxName);
  else next.add(boxName);
  expandedBoxes.value = next;
};
</script>

<template>
  <div class="dashboard">
    <!-- Overview header row -->
    <div class="overview-header" role="status" :aria-label="t('a11y.server_stats')">
      <h2 class="section-title">{{ t('overview.your_servers') }}</h2>
      <div class="overview-counts">
        <NIcon size="14"><PhChartBar weight="duotone" /></NIcon>
        <span>{{ quickStats.servers }} {{ quickStats.servers === 1 ? t('overview.server') : t('overview.servers') }}</span>
        <span class="stats-separator">•</span>
        <span>{{ quickStats.pinnedBoxes }} {{ t('overview.pinned') }}</span>
        <span class="stats-separator">•</span>
        <span>{{ quickStats.favorites }} {{ quickStats.favorites === 1 ? t('overview.favorite') : t('overview.favorites') }}</span>
        <template v-if="lastUpdated">
          <span class="stats-separator">•</span>
          <span class="stats-updated">{{ t('overview.last_updated', { time: lastUpdated.toLocaleTimeString() }) }}</span>
        </template>
      </div>
    </div>

    <!-- Favorites Grid (grouped by box) -->
    <section v-if="allFavorites.length > 0" class="favorites-section">
      <div class="section-header-inline">
        <NIcon size="18" color="var(--warning)"><PhStar weight="fill" /></NIcon>
        <h3>{{ t('overview.favorites_title') }}</h3>
      </div>
      <div v-for="group in favoritesByBox" :key="group.box" class="favorites-group">
        <span class="favorites-group-label">{{ group.box }}</span>
        <div class="favorites-grid">
          <TransitionGroup name="list-row" tag="div" class="favorites-grid-rows">
          <div
            v-for="fav in group.items"
            :key="fav.path"
            class="favorite-tile"
          >
            <span class="favorite-emoji">{{ getEmojiForString(fav.box + ':' + fav.path) }}</span>
            <span class="favorite-text">
              <span class="favorite-path">{{ fav.label }}</span>
              <span v-if="getFavNote(fav.box, fav.path)" class="favorite-subtitle">{{ getFavNote(fav.box, fav.path) }}</span>
            </span>
            <a
              :href="terminalUrl(fav.box, fav.path)"
              class="favorite-btn"
              :title="t('overview.terminal')"
              :aria-label="t('overview.terminal')"
              @click="openInBackgroundTab($event, terminalUrl(fav.box, fav.path))"
            >
              <NIcon size="14"><PhTerminal weight="duotone" /></NIcon>
            </a>
            <a
              :href="filesUrl(fav.box, fav.path)"
              class="favorite-btn"
              :title="t('overview.files')"
              :aria-label="t('overview.files')"
              @click="openInBackgroundTab($event, filesUrl(fav.box, fav.path))"
            >
              <NIcon size="14"><PhFolderOpen weight="duotone" /></NIcon>
            </a>
            <button
              class="fav-settings-btn"
              :title="t('overview.fav_settings_title')"
              :aria-label="t('overview.fav_settings_title')"
              @click.stop="openFavSettings(fav)"
            >
              <NIcon size="14"><PhDotsThreeVertical weight="duotone" /></NIcon>
            </button>
          </div>
          </TransitionGroup>
        </div>
      </div>
    </section>

    <!-- Server Grid -->
    <section class="servers-section">
      <div v-if="boxesStore.loading" class="loading-state">
        <NSpin size="large" />
        <p>{{ t('overview.loading_servers') }}</p>
      </div>

      <NEmpty v-else-if="!hasServers" :description="t('overview.no_servers')">
        <template #extra>
          <NButton type="primary" @click="router.push('/boxes')">
            <template #icon>
              <NIcon><PhPlus weight="duotone" /></NIcon>
            </template>
            {{ t('overview.add_first_server') }}
          </NButton>
        </template>
      </NEmpty>

      <NGrid v-else :x-gap="16" :y-gap="16" cols="1 s:2 l:3" responsive="screen">
        <NGridItem v-for="box in boxesStore.items" :key="box.name">
          <NCard class="server-card" hoverable>
            <div class="server-header">
              <div class="server-status">
                <NIcon size="12" class="status-dot" :class="getServerStatus(box)">
                  <PhCircle weight="fill" />
                </NIcon>
                <span class="server-name">{{ getEmojiForBox(box.name) }} {{ box.name }}</span>
              </div>
              <NTag
                v-if="box.pinned"
                size="small"
                type="info"
              >
                {{ t('overview.favorite_label') }}
              </NTag>
            </div>

            <div class="server-info">
              <p class="server-host">{{ box.host }}</p>

              <!-- System Stats -->
              <div v-if="statsFailure.get(box.name)" class="server-stats-loading">{{ statsFailure.get(box.name) }}</div>
              <div v-else-if="getBoxStats(box.name)" class="server-stats">
                <div class="stat-row">
                  <NIcon size="14" class="stat-icon"><PhCpu weight="duotone" /></NIcon>
                  <span class="stat-label">{{ t('overview.cpu') }}</span>
                  <NProgress
                    type="line"
                    :percentage="getBoxStats(box.name)?.cpu_percent ?? 0"
                    :show-indicator="false"
                    :height="6"
                    :border-radius="3"
                    :color="(getBoxStats(box.name)?.cpu_percent ?? 0) > 80 ? 'var(--error)' : 'var(--accent)'"
                    class="stat-progress"
                  />
                  <span class="stat-value">{{ getBoxStats(box.name)?.cpu_percent?.toFixed(0) ?? '-' }}%</span>
                </div>
                <div class="stat-row">
                  <NIcon size="14" class="stat-icon"><PhMemory weight="duotone" /></NIcon>
                  <span class="stat-label">{{ t('overview.ram') }}</span>
                  <NProgress
                    type="line"
                    :percentage="getBoxStats(box.name)?.memory_percent ?? 0"
                    :show-indicator="false"
                    :height="6"
                    :border-radius="3"
                    :color="(getBoxStats(box.name)?.memory_percent ?? 0) > 80 ? 'var(--error)' : 'var(--success)'"
                    class="stat-progress"
                  />
                  <span class="stat-value">{{ formatMemory(getBoxStats(box.name)?.memory_used_mb) }}</span>
                </div>
                <div v-if="getBoxStats(box.name)?.uptime_seconds" class="stat-uptime">
                  {{ t('overview.uptime') }} {{ formatUptime(getBoxStats(box.name)?.uptime_seconds) }}
                </div>
              </div>
              <div v-else-if="statsLoading.has(box.name)" class="server-stats-loading">
                <NSpin size="small" />
              </div>

              <p class="server-meta">
                <span v-if="getBoxSessions(box.name).length > 0" class="session-link">
                  {{ getBoxSessionCount(box.name) }} {{ getBoxSessionCount(box.name) === 1 ? t('overview.active_session') : t('overview.active_sessions') }}
                </span>
                <span v-else class="no-sessions">{{ t('overview.no_active_sessions') }}</span>
              </p>
              <TransitionGroup
                v-if="getBoxSessions(box.name).length > 0"
                name="list-row"
                tag="div"
                class="session-chips"
              >
                <span
                  v-for="session in visibleSessions(box.name)"
                  :key="session.id"
                  class="session-chip"
                  :class="{ inactive: !session.active }"
                  :title="session.working_directory"
                >
                  <button
                    type="button"
                    class="session-open"
                    @click="openTerminal(box.name, { session: session.session_name })"
                  >
                    <span class="session-status" :class="session.active ? 'active' : 'inactive'"></span>
                    <span class="session-name">{{ session.session_name || t('overview.unnamed') }}</span>
                  </button>
                  <button
                    v-if="session.restorable"
                    type="button"
                    class="restore-layout"
                    @click.stop="restoreLayout(box.name, session)"
                  >
                    {{ t('sessions.restore_layout') }}
                  </button>
                  <span v-if="restoreFailure.get(session.id)" class="restore-failure">
                    {{ restoreFailure.get(session.id) }}
                  </span>
                </span>
                <button
                  v-if="getBoxSessions(box.name).length > CHIP_LIMIT"
                  key="__toggle"
                  type="button"
                  class="sessions-toggle"
                  data-testid="sessions-toggle"
                  @click="toggleSessions(box.name)"
                >
                  {{ expandedBoxes.has(box.name)
                    ? t('overview.show_less')
                    : t('overview.more_sessions', { n: getBoxSessions(box.name).length - CHIP_LIMIT }) }}
                </button>
              </TransitionGroup>
              <p class="server-last-used">
                {{ t('overview.last_used') }} {{ getBoxLastUsed(box.name) }}
              </p>
            </div>

            <BoxSearchInput
              class="server-search"
              :box="box.name"
              :token="tokenValue || null"
              :sessions="getBoxSessions(box.name)"
              @pick-session="openTerminal(box.name, { session: $event })"
              @pick-folder="openTerminal(box.name, { dir: $event })"
            />

            <div class="server-actions">
              <NButton
                type="primary"
                size="small"
                data-testid="box-terminal"
                @click="handleConnectToServer(box.name)"
              >
                {{ t('overview.terminal') }}
              </NButton>
              <NButton
                size="small"
                data-testid="box-files"
                @click="handleBrowseServerFiles(box.name)"
              >
                {{ t('overview.files') }}
              </NButton>
              <NButton
                size="small"
                quaternary
                data-testid="box-multi"
                @click="handleMultiTerminal(box.name)"
              >
                {{ t('boxes.multi') }}
              </NButton>
            </div>
          </NCard>
        </NGridItem>
      </NGrid>
    </section>

    <!-- Favorite Settings Modal -->
    <NModal v-model:show="showFavSettings" :mask-closable="true">
      <NCard
        :title="t('overview.fav_settings_title')"
        style="width: 400px; max-width: 90vw;"
        :bordered="false"
        size="small"
        closable
        @close="showFavSettings = false"
      >
        <div v-if="editingFavorite" class="fav-settings-form">
          <p class="fav-settings-path">{{ editingFavorite.box }}:{{ editingFavorite.path }}</p>
          <label class="fav-settings-label">{{ t('overview.fav_note') }}</label>
          <NInput
            v-model:value="favNoteText"
            type="textarea"
            :placeholder="t('overview.fav_note_placeholder')"
            :autosize="{ minRows: 2, maxRows: 5 }"
          />
        </div>
        <template #footer>
          <div class="fav-settings-actions">
            <NButton size="small" @click="showFavSettings = false">{{ t('common.cancel') }}</NButton>
            <NButton size="small" type="primary" @click="saveFavNote">{{ t('common.save') }}</NButton>
          </div>
        </template>
      </NCard>
    </NModal>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

/* Overview header row */
.overview-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 4px 16px;
}

.overview-counts {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.stats-separator {
  opacity: 0.4;
}

.stats-updated {
  font-size: 11px;
  opacity: 0.6;
}

/* Sections */
.servers-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--text);
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.section-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

/* Loading State */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 48px;
  color: var(--muted);
}

/* Server Cards */
.server-card {
  height: 100%;
  transition: transform var(--dur-base) var(--ease-out), box-shadow var(--dur-base) var(--ease-out);
}

.server-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px var(--shadow-lg);
}

.server-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.server-status {
  display: flex;
  align-items: center;
  gap: 8px;
}

.server-name {
  font-weight: 600;
  font-size: 16px;
}

.server-info {
  margin-bottom: 12px;
}

.server-host {
  margin: 0 0 8px 0;
  color: var(--muted);
  font-size: 14px;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* Server Stats */
.server-stats {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
  padding: 8px;
  background: var(--hover-overlay);
  border-radius: 6px;
}

.server-stats-loading {
  display: flex;
  justify-content: center;
  padding: 8px;
  margin-bottom: 12px;
}

.stat-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stat-icon {
  color: var(--muted);
  flex-shrink: 0;
}

.stat-label {
  font-size: 11px;
  color: var(--muted);
  width: 28px;
  flex-shrink: 0;
}

.stat-progress {
  flex: 1;
  min-width: 60px;
}

.stat-value {
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  color: var(--text);
  width: 42px;
  text-align: right;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.stat-uptime {
  font-variant-numeric: tabular-nums;
  font-size: 10px;
  color: var(--muted);
  text-align: right;
  margin-top: 2px;
}

.server-meta {
  margin: 0 0 4px 0;
  font-size: 14px;
  color: var(--text);
}

.server-last-used {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.server-search {
  margin-bottom: 12px;
}

/* The card that holds the open list paints over the cards below it. */
.server-card:focus-within {
  position: relative;
  z-index: 10;
}

.server-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* Activity Card */
.activity-card {
  height: 100%;
}

.activity-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.activity-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 0;
}

.activity-icon {
  margin-top: 2px;
  color: var(--accent);
}

.activity-content {
  flex: 1;
}

.activity-action {
  margin: 0 0 4px 0;
  font-size: 14px;
  color: var(--text);
}

.activity-meta {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

/* Stats Card */
.stats-card {
  height: 100%;
}

.stats-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.stat-icon {
  color: var(--accent);
}

.stat-number {
  margin: 0 0 2px 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--text);
}

.stat-label {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Responsive Design */
@media (max-width: 767px) {
  .dashboard {
    gap: 16px;
  }

  .overview-counts {
    font-size: 12px;
    flex-wrap: wrap;
    gap: 6px;
  }

  .section-title {
    font-size: 18px;
  }
}

/* Session chips */
.session-link {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.no-sessions {
  color: var(--muted);
}

.session-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.session-chip {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 6px;
  max-width: 100%;
  padding: 2px 4px 2px 2px;
  background: var(--hover-overlay);
  border: 1px solid var(--stroke);
  border-radius: 999px;
  transition: background var(--dur-fast) var(--ease-out);
}

.session-chip:hover {
  background: var(--surface-hover);
}

.session-chip.inactive {
  opacity: 0.7;
}

.session-open {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 8px;
  color: var(--text);
  background: transparent;
  border: none;
  border-radius: 999px;
  cursor: pointer;
  font: inherit;
}

.session-status {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.session-status.active {
  background: var(--success);
}

.session-status.inactive {
  background: var(--muted);
}

.session-name {
  font-weight: 500;
  font-size: 12px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.restore-layout,
.sessions-toggle {
  min-height: 28px;
  padding: 0 10px;
  font-size: 11px;
  color: var(--accent);
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 999px;
  cursor: pointer;
}

.sessions-toggle {
  border-style: dashed;
}

.restore-failure {
  flex-basis: 100%;
  padding-left: 8px;
  font-size: 10px;
  color: var(--error);
}

.status-dot {
  color: var(--muted);
}

.status-dot.online {
  color: var(--success);
}

/* Favorites Section */
.favorites-section {
  margin-bottom: 0;
}

.section-header-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.section-header-inline h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.favorites-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.favorites-group + .favorites-group {
  margin-top: 8px;
}

.favorites-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.favorites-grid-rows {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px;
}

.favorite-tile {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 6px 6px 6px 10px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 10px;
  transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
}

.favorite-tile:hover {
  border-color: var(--stroke-hover);
  box-shadow: 0 2px 8px var(--shadow);
}

.favorite-emoji {
  flex-shrink: 0;
  font-size: 20px;
  line-height: 1;
}

.favorite-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.favorite-path {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.favorite-subtitle {
  font-size: 11px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.favorite-btn,
.fav-settings-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  text-decoration: none;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.favorite-btn:hover,
.fav-settings-btn:hover {
  background: var(--hover-overlay);
  color: var(--text);
}

.favorite-btn:first-of-type {
  color: var(--accent);
}

.fav-settings-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.fav-settings-path {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  font-family: var(--font-mono);
  word-break: break-all;
}

.fav-settings-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.fav-settings-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* Favorites List Card */
.empty-favorites {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  text-align: center;
  color: var(--muted);
}

.empty-favorites p {
  margin: 8px 0 0 0;
}

.empty-hint {
  font-size: 12px;
  opacity: 0.7;
}
</style>
