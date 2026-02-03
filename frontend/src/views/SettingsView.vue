<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { NCard, NButton, NSpace, NAlert, NCode, NInputNumber, NSwitch, useMessage } from 'naive-ui'
import { useBootstrapStore } from '@/stores/bootstrap'
import { useAppStore } from '@/stores/app'
import { http, buildHeaders } from '@/api/http'

const bootstrapStore = useBootstrapStore()
const appStore = useAppStore()
const message = useMessage()

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null)

// Browser globals (safe access for SSR/edge cases)
const location = window.location
const userAgent = window.navigator?.userAgent ?? 'N/A'
const localStorageCount = computed(() => Object.keys(window.localStorage || {}).length)
const sessionStorageCount = computed(() => Object.keys(window.sessionStorage || {}).length)

// Pool configuration state
const poolConfig = ref({
  idle_timeout: null as number | null,
  max_lifetime: null as number | null,
  max_connections_per_box: 3
})
const poolLoading = ref(false)
const poolSaving = ref(false)
const useForeverIdle = ref(false)
const useForeverLifetime = ref(false)

const tokenStatus = computed(() => {
  if (bootstrapStore.loading) return 'Loading...'
  if (bootstrapStore.error) return 'Error: ' + bootstrapStore.error
  if (bootstrapStore.token) return 'Token loaded'
  return 'No token'
})

const refreshToken = async () => {
  try {
    await bootstrapStore.refreshToken()
    message.success('Token refreshed successfully')
  } catch (error) {
    message.error('Failed to refresh token: ' + (error instanceof Error ? error.message : String(error)))
  }
}

const clearCache = () => {
  localStorage.clear()
  sessionStorage.clear()
  message.success('Cache cleared - please refresh the page')
}

const setTheme = (theme: string) => {
  if (appStore.setTheme) {
    appStore.setTheme(theme as any)
  } else {
    message.warning('Theme switching not available')
  }
}

const loadPoolConfig = async () => {
  poolLoading.value = true
  try {
    const response = await http.get('/api/v1/pool/config', {
      headers: buildHeaders(tokenValue.value)
    })
    poolConfig.value = response.data
    useForeverIdle.value = response.data.idle_timeout === null
    useForeverLifetime.value = response.data.max_lifetime === null

    // Convert seconds to minutes for display
    if (response.data.idle_timeout !== null) {
      poolConfig.value.idle_timeout = Math.round(response.data.idle_timeout / 60)
    }
    if (response.data.max_lifetime !== null) {
      poolConfig.value.max_lifetime = Math.round(response.data.max_lifetime / 60)
    }
  } catch (error) {
    message.error('Failed to load pool configuration: ' + (error instanceof Error ? error.message : String(error)))
  } finally {
    poolLoading.value = false
  }
}

const savePoolConfig = async () => {
  poolSaving.value = true
  try {
    const payload = {
      idle_timeout: useForeverIdle.value ? null : (poolConfig.value.idle_timeout || 0) * 60,
      max_lifetime: useForeverLifetime.value ? null : (poolConfig.value.max_lifetime || 0) * 60,
      max_connections_per_box: poolConfig.value.max_connections_per_box
    }

    await http.put('/api/v1/pool/config', payload, {
      headers: buildHeaders(tokenValue.value)
    })
    message.success('Pool configuration updated successfully')
  } catch (error) {
    message.error('Failed to save pool configuration: ' + (error instanceof Error ? error.message : String(error)))
  } finally {
    poolSaving.value = false
  }
}

const formatDuration = (minutes: number | null): string => {
  if (minutes === null) return 'Forever'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
}

onMounted(async () => {
  // Ensure bootstrap is complete before loading pool config
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap()
  }
  loadPoolConfig()
})
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div class="header-content">
        <img src="/logo.png" alt="sshler" class="settings-logo" />
        <div>
          <p class="eyebrow">settings</p>
          <h1>Application Settings</h1>
          <p class="text-muted">Manage your sshler configuration and troubleshoot issues</p>
        </div>
      </div>
    </header>

    <!-- Token Status -->
    <NCard title="Authentication" size="medium">
      <NSpace vertical size="medium">
        <div>
          <p><strong>Status:</strong> {{ tokenStatus }}</p>
          <p v-if="bootstrapStore.token"><strong>Token:</strong> <NCode>{{ bootstrapStore.token.substring(0, 20) }}...</NCode></p>
          <p><strong>Version:</strong> {{ bootstrapStore.version || 'Unknown' }}</p>
        </div>
        
        <NAlert v-if="bootstrapStore.error" type="error" title="Authentication Error">
          {{ bootstrapStore.error }}
          <template #action>
            <NButton size="small" @click="refreshToken">Retry</NButton>
          </template>
        </NAlert>
        
        <NSpace>
          <NButton type="primary" @click="refreshToken" :loading="bootstrapStore.loading">
            Refresh Token
          </NButton>
          <NButton @click="clearCache">
            Clear Cache
          </NButton>
        </NSpace>
      </NSpace>
    </NCard>

    <!-- Theme Settings -->
    <NCard title="Appearance" size="medium">
      <NSpace>
        <NButton
          :type="appStore.isDark ? 'default' : 'primary'"
          @click="setTheme('light')"
        >
          Light Theme
        </NButton>
        <NButton
          :type="appStore.isDark ? 'primary' : 'default'"
          @click="setTheme('dark')"
        >
          Dark Theme
        </NButton>
        <NButton @click="setTheme('system')">
          System Theme
        </NButton>
      </NSpace>
    </NCard>

    <!-- Connection Pool Settings -->
    <NCard title="Connection Pool" size="medium">
      <NSpace vertical size="large">
        <div>
          <p class="text-muted" style="margin-bottom: 16px">
            Configure how long SSH connections are kept alive. Higher values reduce connection overhead but use more resources.
          </p>

          <div class="pool-setting">
            <div class="pool-setting-header">
              <label><strong>Idle Timeout</strong></label>
              <NSwitch v-model:value="useForeverIdle" size="small">
                <template #checked>Forever</template>
                <template #unchecked>Custom</template>
              </NSwitch>
            </div>
            <p class="setting-description">Close connections after this many minutes of inactivity</p>
            <NInputNumber
              v-if="!useForeverIdle"
              v-model:value="poolConfig.idle_timeout"
              :min="1"
              :max="1440"
              :step="5"
              style="width: 200px"
              :disabled="poolLoading || poolSaving"
            >
              <template #suffix>minutes</template>
            </NInputNumber>
            <div v-else class="forever-badge">
              Connections never timeout due to inactivity
            </div>
          </div>

          <div class="pool-setting">
            <div class="pool-setting-header">
              <label><strong>Maximum Lifetime</strong></label>
              <NSwitch v-model:value="useForeverLifetime" size="small">
                <template #checked>Forever</template>
                <template #unchecked>Custom</template>
              </NSwitch>
            </div>
            <p class="setting-description">Close connections after this total lifetime regardless of activity</p>
            <NInputNumber
              v-if="!useForeverLifetime"
              v-model:value="poolConfig.max_lifetime"
              :min="1"
              :max="1440"
              :step="5"
              style="width: 200px"
              :disabled="poolLoading || poolSaving"
            >
              <template #suffix>minutes</template>
            </NInputNumber>
            <div v-else class="forever-badge">
              Connections never expire based on age
            </div>
          </div>

          <div class="pool-setting">
            <label><strong>Max Connections Per Box</strong></label>
            <p class="setting-description">Maximum number of pooled connections per server</p>
            <NInputNumber
              v-model:value="poolConfig.max_connections_per_box"
              :min="1"
              :max="10"
              style="width: 200px"
              :disabled="poolLoading || poolSaving"
            >
              <template #suffix>connections</template>
            </NInputNumber>
          </div>
        </div>

        <NAlert v-if="poolLoading" type="info">
          Loading pool configuration...
        </NAlert>

        <NSpace>
          <NButton
            type="primary"
            @click="savePoolConfig"
            :loading="poolSaving"
            :disabled="poolLoading"
          >
            Save Pool Settings
          </NButton>
          <NButton
            @click="loadPoolConfig"
            :disabled="poolLoading || poolSaving"
          >
            Reset
          </NButton>
        </NSpace>

        <NAlert type="info" title="Connection Pool Info" :bordered="false">
          <p><strong>Current Settings:</strong></p>
          <ul>
            <li>Idle Timeout: {{ useForeverIdle ? 'Forever' : formatDuration(poolConfig.idle_timeout) }}</li>
            <li>Max Lifetime: {{ useForeverLifetime ? 'Forever' : formatDuration(poolConfig.max_lifetime) }}</li>
            <li>Max Connections: {{ poolConfig.max_connections_per_box }} per box</li>
          </ul>
        </NAlert>
      </NSpace>
    </NCard>

    <!-- Debug Info -->
    <NCard title="Debug Information" size="medium">
      <NSpace vertical size="small">
        <p><strong>Current URL:</strong> {{ location.href }}</p>
        <p><strong>User Agent:</strong> {{ userAgent }}</p>
        <p><strong>Local Storage:</strong> {{ localStorageCount }} items</p>
        <p><strong>Session Storage:</strong> {{ sessionStorageCount }} items</p>
      </NSpace>
    </NCard>

    <!-- Troubleshooting -->
    <NCard title="Troubleshooting" size="medium">
      <NSpace vertical size="medium">
        <div>
          <h3>Common Issues</h3>
          <ul>
            <li><strong>Missing token errors:</strong> Click "Refresh Token" above</li>
            <li><strong>UI not updating:</strong> Click "Clear Cache" and refresh the page</li>
            <li><strong>Terminal not connecting:</strong> Check that the box is online and accessible</li>
          </ul>
        </div>
        
        <NAlert type="info" title="Need Help?">
          If you're still having issues, try running <NCode>sshler fix</NCode> in your terminal to rebuild the frontend.
        </NAlert>
      </NSpace>
    </NCard>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.header-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.settings-logo {
  width: 200px;
  height: auto;
  max-height: 120px;
  object-fit: contain;
  border-radius: 12px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-size: 12px;
  margin: 0 0 4px 0;
  color: var(--muted);
}

h1 {
  margin: 0 0 8px 0;
  font-size: 26px;
}

.text-muted {
  color: var(--muted);
  margin: 0;
}

h3 {
  margin: 0 0 8px 0;
  font-size: 16px;
}

ul {
  margin: 0;
  padding-left: 20px;
}

li {
  margin-bottom: 4px;
}

.pool-setting {
  margin-bottom: 24px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--divider);
}

.pool-setting:last-of-type {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.pool-setting-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.pool-setting label {
  display: block;
  margin-bottom: 4px;
}

.setting-description {
  font-size: 13px;
  color: var(--muted);
  margin: 0 0 12px 0;
}

.forever-badge {
  padding: 8px 12px;
  background: var(--accent-bg, rgba(100, 160, 255, 0.1));
  color: var(--accent, #6aa6ff);
  border-radius: 4px;
  font-size: 13px;
  font-weight: 500;
}
</style>
