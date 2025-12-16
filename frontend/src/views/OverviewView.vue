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
  NTime,
  useMessage
} from "naive-ui";
import {
  PhPlus,
  PhTerminal,
  PhFolderOpen,
  PhRocketLaunch,
  PhCircle,
  PhClockCounterClockwise,
  PhChartBar,
  PhDesktop,
  PhFiles,
  PhLightning
} from "@phosphor-icons/vue";

import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";

const router = useRouter();
const message = useMessage();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token);
const hasServers = computed(() => boxesStore.items.length > 0);
const onlineServers = computed(() => boxesStore.items.filter(box => !box.name.includes('offline')));
const recentServer = computed(() => boxesStore.items.find(box => box.pinned) || boxesStore.items[0]);

// Mock activity data - in real app this would come from API
const recentActivity = computed(() => [
  { type: 'terminal', server: 'dev-server', action: 'Opened terminal session', time: new Date(Date.now() - 5 * 60 * 1000) },
  { type: 'file', server: 'prod-web', action: 'Uploaded config.json', time: new Date(Date.now() - 2 * 60 * 60 * 1000) },
  { type: 'session', server: 'dev-server', action: 'Started session "deploy"', time: new Date(Date.now() - 4 * 60 * 60 * 1000) },
]);

const quickStats = computed(() => ({
  servers: boxesStore.items.length,
  filesUploaded: 12, // Mock data
  activeSessions: 5   // Mock data
}));

onMounted(async () => {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null);
  }
});

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

const getActivityIcon = (type: string) => {
  switch (type) {
    case 'terminal': return PhTerminal;
    case 'file': return PhFiles;
    case 'session': return PhLightning;
    default: return PhCircle;
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
                {{ getServerStatus(box) === 'online' ? '2 active sessions' : 
                   getServerStatus(box) === 'busy' ? '1 active session' : 'offline' }}
              </p>
              <p class="server-last-used">
                Last used: {{ getServerStatus(box) === 'online' ? '5min ago' : 
                            getServerStatus(box) === 'busy' ? '2hr ago' : '2d ago' }}
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
      <!-- Recent Activity -->
      <NGridItem>
        <NCard class="activity-card">
          <div class="section-header">
            <NIcon size="18">
              <PhClockCounterClockwise />
            </NIcon>
            <h3>Recent Activity</h3>
          </div>
          
          <div class="activity-list">
            <div 
              v-for="(activity, index) in recentActivity" 
              :key="index"
              class="activity-item"
            >
              <NIcon size="16" class="activity-icon">
                <component :is="getActivityIcon(activity.type)" />
              </NIcon>
              <div class="activity-content">
                <p class="activity-action">{{ activity.action }}</p>
                <p class="activity-meta">
                  {{ activity.server }} • <NTime :time="activity.time" type="relative" />
                </p>
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
                <PhFiles />
              </NIcon>
              <div>
                <p class="stat-number">{{ quickStats.filesUploaded }}</p>
                <p class="stat-label">Files Today</p>
              </div>
            </div>
            
            <div class="stat-item">
              <NIcon size="20" class="stat-icon">
                <PhLightning />
              </NIcon>
              <div>
                <p class="stat-number">{{ quickStats.activeSessions }}</p>
                <p class="stat-label">Sessions</p>
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
</style>
