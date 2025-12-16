<script setup lang="ts">
import { computed } from 'vue'
import { NCard, NButton, NSpace, NAlert, NCode, useMessage } from 'naive-ui'
import { useBootstrapStore } from '@/stores/bootstrap'
import { useAppStore } from '@/stores/app'

const bootstrapStore = useBootstrapStore()
const appStore = useAppStore()
const message = useMessage()
const location = window.location

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
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <p class="eyebrow">settings</p>
        <h1>Application Settings</h1>
        <p class="text-muted">Manage your sshler configuration and troubleshoot issues</p>
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

    <!-- Debug Info -->
    <NCard title="Debug Information" size="medium">
      <NSpace vertical size="small">
        <p><strong>Current URL:</strong> {{ location.href }}</p>
        <p><strong>User Agent:</strong> {{ navigator.userAgent }}</p>
        <p><strong>Local Storage:</strong> {{ Object.keys(localStorage).length }} items</p>
        <p><strong>Session Storage:</strong> {{ Object.keys(sessionStorage).length }} items</p>
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
</style>
