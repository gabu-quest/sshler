<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import {
  NButton,
  NCard,
  NGrid,
  NGridItem,
  NIcon,
  NSpin,
  NTag,
  NEmpty,
  NTooltip,
  NProgress,
  useMessage
} from "naive-ui";
import {
  PhPlus,
  PhTerminal,
  PhFolderOpen,
  PhFolder,
  PhCircle,
  PhChartBar,
  PhStar,
  PhCpu,
  PhMemory
} from "@phosphor-icons/vue";

import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useSessionsStore } from "@/stores/sessions";
import { useFavoritesStore } from "@/stores/favorites";
import { boxStats } from "@/api/http";
import type { BoxStats } from "@/api/types";
import { resetFavicon } from "@/utils/emoji-favicon";

const router = useRouter();
const message = useMessage();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const sessionsStore = useSessionsStore();
const favoritesStore = useFavoritesStore();

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token);
const hasServers = computed(() => boxesStore.items.length > 0);
const recentServer = computed(() => boxesStore.items.find(box => box.pinned) || boxesStore.items[0]);
const pinnedBoxes = computed(() => boxesStore.items.filter(box => box.pinned));
const totalFavorites = computed(() =>
  boxesStore.items.reduce((acc, box) => acc + (box.favorites?.length || 0), 0)
);

// System stats per box
const statsMap = ref<Map<string, BoxStats>>(new Map());
const statsLoading = ref<Set<string>>(new Set());

async function loadBoxStats(boxName: string) {
  if (statsLoading.value.has(boxName)) return;
  statsLoading.value.add(boxName);
  try {
    const stats = await boxStats(boxName, tokenValue.value || null);
    statsMap.value.set(boxName, stats);
  } catch (e) {
    // Stats are optional, don't break the UI
  } finally {
    statsLoading.value.delete(boxName);
  }
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

// Flatten all favorites across all boxes for display
const allFavorites = computed(() => {
  const favorites: Array<{ box: string; path: string; label: string }> = []
  boxesStore.items.forEach(box => {
    // Get favorites from box data
    const boxFavs = box.favorites || []
    boxFavs.forEach(path => {
      const label = path.split('/').pop() || path
      favorites.push({ box: box.name, path, label })
    })
    // Also get from favorites store (may have additional ones)
    const storeFavs = Array.from(favoritesStore.favoritesForBox(box.name).values())
    storeFavs.forEach(path => {
      if (!favorites.some(f => f.box === box.name && f.path === path)) {
        const label = path.split('/').pop() || path
        favorites.push({ box: box.name, path, label })
      }
    })
  })
  return favorites
});

// Build URLs for favorites (used as href for middle-click support)
const getFavoriteTerminalUrl = (box: string, path: string) => {
  return `/app/terminal?box=${encodeURIComponent(box)}&dir=${encodeURIComponent(path)}`
};

const getFavoriteFilesUrl = (box: string, path: string) => {
  return `/app/files?box=${encodeURIComponent(box)}&path=${encodeURIComponent(path)}`
};

// Handle left-click: open in background tab (don't switch focus)
const openInBackgroundTab = (event: MouseEvent, url: string) => {
  event.preventDefault();
  const newTab = window.open(url, '_blank');
  // Try to keep focus on current tab (not always reliable)
  window.focus();
};

// Real stats only - no fake data
const quickStats = computed(() => ({
  servers: boxesStore.items.length,
  pinnedBoxes: pinnedBoxes.value.length,
  favorites: totalFavorites.value
}));

onMounted(async () => {
  // Reset to default favicon on overview page
  document.title = 'Overview';
  resetFavicon();

  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null);
  }
  // Sync sessions with actual tmux state (marks stale sessions inactive)
  if (boxesStore.items.length > 0) {
    await sessionsStore.syncAllBoxSessions(
      boxesStore.items.map(b => b.name),
      tokenValue.value || null
    );
    // Load stats for all boxes in parallel
    boxesStore.items.forEach(box => loadBoxStats(box.name));
  }
});

// Get session info for a box
const getBoxSessionCount = (boxName: string): number => {
  return sessionsStore.getActiveSessionCount(boxName);
};

const getBoxLastUsed = (boxName: string): string => {
  const session = sessionsStore.getMostRecentSession(boxName);
  if (!session) return 'never';
  return sessionsStore.formatRelativeTime(session.last_accessed_at);
};

const getBoxSessions = (boxName: string) => {
  return sessionsStore.getBoxSessions(boxName);
};

const handleJumpToSession = (boxName: string, sessionName: string) => {
  router.push(`/terminal?box=${boxName}&session=${sessionName}`);
};

const getServerStatus = (box: any) => {
  if (box.name.includes('offline')) return 'offline';
  if (box.name.includes('prod')) return 'busy';
  return 'online';
};

const getStatusColor = (status: string) => {
  switch (status) {
    case 'online': return '#52c41a';
    case 'busy': return '#faad14';
    case 'offline': return '#8c8c8c';
    default: return '#8c8c8c';
  }
};

const handleQuickConnect = () => {
  if (recentServer.value) {
    router.push(`/terminal?box=${recentServer.value.name}`);
  } else {
    message.warning('No servers available');
  }
};

const handleBrowseFiles = () => {
  if (recentServer.value) {
    router.push(`/files?box=${recentServer.value.name}`);
  } else {
    message.warning('No servers available');
  }
};

const handleNewTerminal = () => {
  router.push('/terminal');
};

const handleConnectToServer = (serverName: string) => {
  router.push(`/terminal?box=${serverName}`);
};

const handleBrowseServerFiles = (serverName: string) => {
  router.push(`/files?box=${serverName}`);
};
</script>

<template>
  <div class="dashboard">
    <!-- Compact Stats Pill -->
    <div class="stats-pill">
      <NIcon size="14"><PhChartBar /></NIcon>
      <span>{{ quickStats.servers }} server{{ quickStats.servers === 1 ? '' : 's' }}</span>
      <span class="stats-separator">•</span>
      <span>{{ quickStats.pinnedBoxes }} pinned</span>
      <span class="stats-separator">•</span>
      <span>{{ quickStats.favorites }} favorite{{ quickStats.favorites === 1 ? '' : 's' }}</span>
    </div>

    <!-- Favorites Bar -->
    <section v-if="allFavorites.length > 0" class="favorites-section">
      <div class="section-header-inline">
        <NIcon size="18" color="#faad14"><PhStar weight="fill" /></NIcon>
        <h3>Favorites</h3>
      </div>
      <div class="favorites-list">
        <div
          v-for="fav in allFavorites"
          :key="fav.box + '-' + fav.path"
          class="favorite-item"
        >
          <div class="favorite-info">
            <span class="favorite-box">{{ fav.box }}</span>
            <span class="favorite-path">{{ fav.label }}</span>
          </div>
          <div class="favorite-actions">
            <a
              :href="getFavoriteTerminalUrl(fav.box, fav.path)"
              class="favorite-btn favorite-btn-terminal"
              title="Open terminal (middle-click for background tab)"
              @click="openInBackgroundTab($event, getFavoriteTerminalUrl(fav.box, fav.path))"
            >
              <NIcon size="14"><PhTerminal /></NIcon>
              <span>Terminal</span>
            </a>
            <a
              :href="getFavoriteFilesUrl(fav.box, fav.path)"
              class="favorite-btn favorite-btn-files"
              title="Browse files (middle-click for background tab)"
              @click="openInBackgroundTab($event, getFavoriteFilesUrl(fav.box, fav.path))"
            >
              <NIcon size="14"><PhFolderOpen /></NIcon>
              <span>Files</span>
            </a>
          </div>
        </div>
      </div>
    </section>

    <!-- Server Grid -->
    <section class="servers-section">
      <h2 class="section-title">Your Servers</h2>
      
      <div v-if="boxesStore.loading" class="loading-state">
        <NSpin size="large" />
        <p>Loading your servers...</p>
      </div>
      
      <NEmpty v-else-if="!hasServers" description="No servers configured">
        <template #extra>
          <NButton type="primary" @click="router.push('/boxes')">
            <template #icon>
              <NIcon><PhPlus /></NIcon>
            </template>
            Add Your First Server
          </NButton>
        </template>
      </NEmpty>
      
      <NGrid v-else :x-gap="16" :y-gap="16" cols="1 s:2 l:3" responsive="screen">
        <NGridItem v-for="box in boxesStore.items" :key="box.name">
          <NCard class="server-card" hoverable>
            <div class="server-header">
              <div class="server-status">
                <NIcon 
                  size="12" 
                  :color="getStatusColor(getServerStatus(box))"
                >
                  <PhCircle />
                </NIcon>
                <span class="server-name">{{ box.name }}</span>
              </div>
              <NTag 
                v-if="box.pinned" 
                size="small" 
                type="info"
              >
                Favorite
              </NTag>
            </div>
            
            <div class="server-info">
              <p class="server-host">{{ box.host }}</p>

              <!-- System Stats -->
              <div v-if="getBoxStats(box.name)" class="server-stats">
                <div class="stat-row">
                  <NIcon size="14" class="stat-icon"><PhCpu /></NIcon>
                  <span class="stat-label">CPU</span>
                  <NProgress
                    type="line"
                    :percentage="getBoxStats(box.name)?.cpu_percent ?? 0"
                    :show-indicator="false"
                    :height="6"
                    :border-radius="3"
                    :color="(getBoxStats(box.name)?.cpu_percent ?? 0) > 80 ? '#ef4444' : '#3b82f6'"
                    class="stat-progress"
                  />
                  <span class="stat-value">{{ getBoxStats(box.name)?.cpu_percent?.toFixed(0) ?? '-' }}%</span>
                </div>
                <div class="stat-row">
                  <NIcon size="14" class="stat-icon"><PhMemory /></NIcon>
                  <span class="stat-label">RAM</span>
                  <NProgress
                    type="line"
                    :percentage="getBoxStats(box.name)?.memory_percent ?? 0"
                    :show-indicator="false"
                    :height="6"
                    :border-radius="3"
                    :color="(getBoxStats(box.name)?.memory_percent ?? 0) > 80 ? '#ef4444' : '#22c55e'"
                    class="stat-progress"
                  />
                  <span class="stat-value">{{ formatMemory(getBoxStats(box.name)?.memory_used_mb) }}</span>
                </div>
                <div v-if="getBoxStats(box.name)?.uptime_seconds" class="stat-uptime">
                  Up: {{ formatUptime(getBoxStats(box.name)?.uptime_seconds) }}
                </div>
              </div>
              <div v-else-if="statsLoading.has(box.name)" class="server-stats-loading">
                <NSpin size="small" />
              </div>

              <p class="server-meta">
                <NTooltip v-if="getBoxSessions(box.name).length > 0" trigger="hover">
                  <template #trigger>
                    <span class="session-link">
                      {{ getBoxSessionCount(box.name) }} active session{{ getBoxSessionCount(box.name) === 1 ? '' : 's' }}
                    </span>
                  </template>
                  <div class="session-tooltip">
                    <div
                      v-for="session in getBoxSessions(box.name)"
                      :key="session.id"
                      class="session-item"
                      :class="{ inactive: !session.active }"
                      @click="handleJumpToSession(box.name, session.session_name)"
                    >
                      <span class="session-row">
                        <span class="session-status" :class="session.active ? 'active' : 'inactive'"></span>
                        <span class="session-name">{{ session.session_name || 'unnamed' }}</span>
                      </span>
                      <span class="session-dir">{{ session.working_directory }}</span>
                    </div>
                  </div>
                </NTooltip>
                <span v-else class="no-sessions">No active sessions</span>
              </p>
              <p class="server-last-used">
                Last used: {{ getBoxLastUsed(box.name) }}
              </p>
            </div>
            
            <div class="server-actions">
              <NButton 
                type="primary" 
                size="small"
                @click="handleConnectToServer(box.name)"
                :disabled="getServerStatus(box) === 'offline'"
              >
                Connect
              </NButton>
              <NButton 
                size="small"
                @click="handleBrowseServerFiles(box.name)"
                :disabled="getServerStatus(box) === 'offline'"
              >
                Files
              </NButton>
            </div>
          </NCard>
        </NGridItem>
      </NGrid>
    </section>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 32px;
  max-width: 1200px;
  margin: 0 auto;
}

/* Compact Stats Pill */
.stats-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 20px;
  font-size: 13px;
  color: var(--muted);
  width: fit-content;
}

.stats-separator {
  opacity: 0.4;
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
  transition: all 0.2s ease;
}

.server-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
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
  margin-bottom: 16px;
}

.server-host {
  margin: 0 0 8px 0;
  color: var(--muted);
  font-size: 14px;
  font-family: 'JetBrains Mono', monospace;
}

/* Server Stats */
.server-stats {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
  padding: 8px;
  background: rgba(255, 255, 255, 0.02);
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
  font-size: 11px;
  color: var(--text);
  width: 42px;
  text-align: right;
  font-family: 'JetBrains Mono', monospace;
}

.stat-uptime {
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

.server-actions {
  display: flex;
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
@media (max-width: 768px) {
  .dashboard {
    gap: 24px;
  }

  .stats-pill {
    font-size: 12px;
    flex-wrap: wrap;
    gap: 6px;
  }

  .favorite-item {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }

  .favorite-actions {
    width: 100%;
  }

  .favorite-btn {
    flex: 1;
    justify-content: center;
  }

  .section-title {
    font-size: 18px;
  }
}

@media (max-width: 480px) {
  .stats-pill {
    width: 100%;
    justify-content: center;
  }
}

/* Dark mode adjustments */
@media (prefers-color-scheme: dark) {
  .server-card:hover {
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
  }
}

/* Session tooltip styles */
.session-link {
  cursor: pointer;
  color: var(--accent);
  text-decoration: underline dotted;
}

.session-link:hover {
  text-decoration: underline solid;
}

.no-sessions {
  color: var(--muted);
}

.session-tooltip {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 180px;
  max-height: 280px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
}

.session-tooltip::-webkit-scrollbar {
  width: 6px;
}

.session-tooltip::-webkit-scrollbar-track {
  background: transparent;
}

.session-tooltip::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 3px;
}

.session-tooltip::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.3);
}

.session-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 4px 8px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.session-item:hover {
  background: rgba(255, 255, 255, 0.16);
}

.session-item.inactive {
  opacity: 0.6;
}

.session-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.session-status {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.session-status.active {
  background: #22c55e;
}

.session-status.inactive {
  background: #6b7280;
}

.session-name {
  font-weight: 500;
  font-size: 12px;
}

.session-dir {
  font-size: 10px;
  opacity: 0.6;
  font-family: var(--font-mono);
  margin-left: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}

/* Favorites Section */
.favorites-section {
  margin-bottom: 16px;
}

.section-header-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.section-header-inline h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.favorites-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.favorite-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  transition: border-color 0.15s ease;
}

.favorite-item:hover {
  border-color: var(--stroke);
}

.favorite-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.favorite-box {
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.favorite-path {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.favorite-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.favorite-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
  transition: all 0.15s ease;
  cursor: pointer;
}

.favorite-btn-terminal {
  background: rgba(59, 130, 246, 0.15);
  color: #3b82f6;
  border: 1px solid rgba(59, 130, 246, 0.3);
}

.favorite-btn-terminal:hover {
  background: rgba(59, 130, 246, 0.25);
  border-color: rgba(59, 130, 246, 0.5);
}

.favorite-btn-files {
  background: rgba(34, 197, 94, 0.15);
  color: #22c55e;
  border: 1px solid rgba(34, 197, 94, 0.3);
}

.favorite-btn-files:hover {
  background: rgba(34, 197, 94, 0.25);
  border-color: rgba(34, 197, 94, 0.5);
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
