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
  PhRocketLaunch,
  PhCircle,
  PhChartBar,
  PhDesktop,
  PhLightning
} from "@phosphor-icons/vue";

import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useSessionsStore } from "@/stores/sessions";

const router = useRouter();
const message = useMessage();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const sessionsStore = useSessionsStore();

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token);
const hasServers = computed(() => boxesStore.items.length > 0);
const onlineServers = computed(() => boxesStore.items.filter(box => !box.name.includes('offline')));
const recentServer = computed(() => boxesStore.items.find(box => box.pinned) || boxesStore.items[0]);
const pinnedBoxes = computed(() => boxesStore.items.filter(box => box.pinned));
const totalFavorites = computed(() => 
  boxesStore.items.reduce((acc, box) => acc + (box.favorites?.length || 0), 0)
);

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
    <!-- Hero Section -->
    <section class="hero">
      <div class="hero-content">
        <h1 class="hero-title">
          <NIcon size="24" class="hero-icon">
            <PhRocketLaunch />
          </NIcon>
          Welcome back!
        </h1>
        <p class="hero-subtitle">
          <template v-if="hasServers">
            Ready to connect? You have {{ boxesStore.items.length }} server{{ boxesStore.items.length === 1 ? '' : 's' }} available
          </template>
          <template v-else>
            Add your first server to get started
          </template>
        </p>
      </div>
      
      <div class="hero-actions">
        <NButton 
          type="primary" 
          size="large" 
          @click="handleQuickConnect"
          :disabled="!hasServers"
          class="hero-btn"
        >
          <template #icon>
            <NIcon><PhTerminal /></NIcon>
          </template>
          Quick Connect
        </NButton>
        
        <NButton 
          size="large" 
          @click="handleBrowseFiles"
          :disabled="!hasServers"
          class="hero-btn"
        >
          <template #icon>
            <NIcon><PhFolderOpen /></NIcon>
          </template>
          Browse Files
        </NButton>
        
        <NButton 
          size="large" 
          @click="handleNewTerminal"
          class="hero-btn"
        >
          <template #icon>
            <NIcon><PhLightning /></NIcon>
          </template>
          New Terminal
        </NButton>
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

    <!-- Activity & Stats -->
    <NGrid :x-gap="24" :y-gap="16" :cols="2" responsive="screen">
      <!-- Quick Actions -->
      <NGridItem>
        <NCard class="activity-card">
          <div class="section-header">
            <NIcon size="18">
              <PhRocketLaunch />
            </NIcon>
            <h3>Quick Actions</h3>
          </div>
          
          <div class="activity-list">
            <div class="activity-item" @click="router.push('/terminal')" style="cursor: pointer;">
              <NIcon size="16" class="activity-icon">
                <PhTerminal />
              </NIcon>
              <div class="activity-content">
                <p class="activity-action">Open Multi-Terminal</p>
                <p class="activity-meta">Manage multiple sessions in a grid</p>
              </div>
            </div>
            <div class="activity-item" @click="router.push('/boxes')" style="cursor: pointer;">
              <NIcon size="16" class="activity-icon">
                <PhDesktop />
              </NIcon>
              <div class="activity-content">
                <p class="activity-action">Manage Boxes</p>
                <p class="activity-meta">View and configure your servers</p>
              </div>
            </div>
            <div class="activity-item" @click="router.push('/settings')" style="cursor: pointer;">
              <NIcon size="16" class="activity-icon">
                <PhLightning />
              </NIcon>
              <div class="activity-content">
                <p class="activity-action">Settings</p>
                <p class="activity-meta">Configure connection pool and preferences</p>
              </div>
            </div>
          </div>
        </NCard>
      </NGridItem>
      
      <!-- Quick Stats -->
      <NGridItem>
        <NCard class="stats-card">
          <div class="section-header">
            <NIcon size="18">
              <PhChartBar />
            </NIcon>
            <h3>At a Glance</h3>
          </div>
          
          <div class="stats-grid">
            <div class="stat-item">
              <NIcon size="20" class="stat-icon">
                <PhDesktop />
              </NIcon>
              <div>
                <p class="stat-number">{{ quickStats.servers }}</p>
                <p class="stat-label">Servers</p>
              </div>
            </div>
            
            <div class="stat-item">
              <NIcon size="20" class="stat-icon">
                <PhCircle />
              </NIcon>
              <div>
                <p class="stat-number">{{ quickStats.pinnedBoxes }}</p>
                <p class="stat-label">Pinned</p>
              </div>
            </div>
            
            <div class="stat-item">
              <NIcon size="20" class="stat-icon">
                <PhFolderOpen />
              </NIcon>
              <div>
                <p class="stat-number">{{ quickStats.favorites }}</p>
                <p class="stat-label">Favorites</p>
              </div>
            </div>
          </div>
        </NCard>
      </NGridItem>
    </NGrid>
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

/* Hero Section */
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 24px;
  background: linear-gradient(135deg, rgba(124, 93, 255, 0.1) 0%, rgba(124, 93, 255, 0.05) 100%);
  border-radius: 16px;
  border: 1px solid rgba(124, 93, 255, 0.2);
}

.hero-content {
  flex: 1;
}

.hero-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--text);
}

.hero-icon {
  color: var(--accent);
}

.hero-subtitle {
  margin: 0;
  font-size: 16px;
  color: var(--muted);
  line-height: 1.5;
}

.hero-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.hero-btn {
  min-width: 140px;
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
</style>
