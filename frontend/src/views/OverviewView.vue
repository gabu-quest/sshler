<script setup lang="ts">
import { computed, onMounted } from "vue";
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
  useMessage
} from "naive-ui";
import {
  PhPlus,
  PhTerminal,
  PhFolderOpen,
  PhFolder,
  PhCircle,
  PhChartBar,
  PhStar
} from "@phosphor-icons/vue";

import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useSessionsStore } from "@/stores/sessions";
import { useFavoritesStore } from "@/stores/favorites";

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

// Open favorite in new tab
const openFavoriteTerminal = (box: string, path: string) => {
  const url = `/app/terminal?box=${encodeURIComponent(box)}&dir=${encodeURIComponent(path)}`
  window.open(url, '_blank')
};

const openFavoriteFiles = (box: string, path: string) => {
  const url = `/app/files?box=${encodeURIComponent(box)}&path=${encodeURIComponent(path)}`
  window.open(url, '_blank')
};

// Real stats only - no fake data
const quickStats = computed(() => ({
  servers: boxesStore.items.length,
  pinnedBoxes: pinnedBoxes.value.length,
  favorites: totalFavorites.value
}));

onMounted(async () => {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null);
  }
  // Load sessions for all boxes
  if (boxesStore.items.length > 0) {
    await sessionsStore.loadAllBoxSessions(
      boxesStore.items.map(b => b.name),
      tokenValue.value || null
    );
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

    <!-- Favorites At a Glance Row -->
    <section v-if="allFavorites.length > 0" class="favorites-row-section">
      <div class="section-header-inline">
        <NIcon size="18" color="#faad14"><PhStar weight="fill" /></NIcon>
        <h3>Favorites</h3>
      </div>
      <div class="favorites-row">
        <div 
          v-for="fav in allFavorites" 
          :key="fav.box + '-' + fav.path"
          class="favorite-chip"
          @click="openFavoriteTerminal(fav.box, fav.path)"
          title="Click to open terminal in new tab"
        >
          <span class="chip-box">{{ fav.box }}</span>
          <span class="chip-path">{{ fav.label }}</span>
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
      
      <NGrid v-else :x-gap="16" :y-gap="16" :cols="3" responsive="screen">
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
                      @click="handleJumpToSession(box.name, session.session_name)"
                    >
                      <span class="session-name">{{ session.session_name || 'unnamed' }}</span>
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

    <!-- Favorites Section (only shown if no favorites in chip row OR has many favorites) -->
    <section v-if="allFavorites.length > 0" class="favorites-section">
      <NCard class="activity-card">
        <div class="section-header">
          <NIcon size="18" color="#faad14">
            <PhStar weight="fill" />
          </NIcon>
          <h3>All Favorites</h3>
        </div>
        
        <div class="favorites-list">
          <div 
            v-for="fav in allFavorites" 
            :key="fav.box + '-' + fav.path"
            class="favorite-item"
          >
            <div class="favorite-info">
              <NIcon size="16" color="#faad14"><PhStar weight="fill" /></NIcon>
              <div class="favorite-text">
                <p class="favorite-path">{{ fav.label }}</p>
                <p class="favorite-box">{{ fav.box }} • {{ fav.path }}</p>
              </div>
            </div>
            <div class="favorite-actions">
              <NButton 
                size="tiny" 
                type="primary"
                @click="openFavoriteTerminal(fav.box, fav.path)"
                title="Open terminal in new tab"
              >
                <template #icon><NIcon><PhTerminal /></NIcon></template>
              </NButton>
              <NButton 
                size="tiny"
                @click="openFavoriteFiles(fav.box, fav.path)"
                title="Open files in new tab"
              >
                <template #icon><NIcon><PhFolder /></NIcon></template>
              </NButton>
            </div>
          </div>
        </div>
      </NCard>
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

/* Favorites Section */
.favorites-section {
  margin-top: -16px;
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
  margin: 0 0 4px 0;
  color: var(--muted);
  font-size: 14px;
  font-family: 'JetBrains Mono', monospace;
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
  .hero {
    flex-direction: column;
    text-align: center;
  }
  
  .hero-actions {
    justify-content: center;
  }
  
  .hero-btn {
    min-width: 120px;
  }
  
  .dashboard {
    gap: 24px;
  }
}

@media (max-width: 480px) {
  .hero-actions {
    flex-direction: column;
    width: 100%;
  }
  
  .hero-btn {
    width: 100%;
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
  gap: 8px;
  min-width: 200px;
}

.session-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.session-item:hover {
  background: rgba(255, 255, 255, 0.2);
}

.session-name {
  font-weight: 600;
  font-size: 13px;
}

.session-dir {
  font-size: 11px;
  opacity: 0.7;
  font-family: var(--font-mono);
}

/* Favorites Row Section (At a Glance) */
.favorites-row-section {
  margin-bottom: 8px;
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

.favorites-row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding: 4px 0 8px 0;
}

.favorite-chip {
  display: flex;
  flex-direction: column;
  padding: 10px 14px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}

.favorite-chip:hover {
  border-color: var(--accent);
  background: rgba(52, 152, 219, 0.1);
  transform: translateY(-2px);
}

.chip-box {
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.chip-path {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
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

.favorites-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.favorite-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 8px;
  transition: background 0.15s ease;
}

.favorite-item:hover {
  background: rgba(255, 255, 255, 0.06);
}

.favorite-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.favorite-text {
  min-width: 0;
}

.favorite-path {
  margin: 0;
  font-weight: 600;
  font-size: 13px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.favorite-box {
  margin: 2px 0 0 0;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.favorite-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.favorites-more {
  text-align: center;
  padding: 8px;
  font-size: 12px;
  color: var(--muted);
}
</style>
