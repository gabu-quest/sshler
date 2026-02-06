<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { NCard, NButton, NSpace, NAlert, NCode, NInputNumber, NSwitch, useMessage } from 'naive-ui'
import { useBootstrapStore } from '@/stores/bootstrap'
import { useAppStore } from '@/stores/app'
import { http, buildHeaders } from '@/api/http'
import { resetFavicon } from '@/utils/emoji-favicon'
import { useI18n } from '@/i18n'

const bootstrapStore = useBootstrapStore()
const appStore = useAppStore()
const message = useMessage()
const { t } = useI18n()

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
    message.success(t('settings.token_refreshed'))
  } catch (error) {
    message.error(t('settings.token_refresh_failed') + ' ' + (error instanceof Error ? error.message : String(error)))
  }
}

const clearCache = () => {
  localStorage.clear()
  sessionStorage.clear()
  message.success(t('settings.cache_cleared'))
}

const setTheme = (theme: string) => {
  if (appStore.setTheme) {
    appStore.setTheme(theme as any)
  } else {
    message.warning(t('settings.theme_unavailable'))
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
    message.error(t('settings.pool_load_failed') + ' ' + (error instanceof Error ? error.message : String(error)))
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
    message.success(t('settings.pool_saved'))
  } catch (error) {
    message.error(t('settings.pool_save_failed') + ' ' + (error instanceof Error ? error.message : String(error)))
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
  // Reset to default favicon on settings page
  document.title = 'Settings'
  resetFavicon()

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
          <p class="eyebrow">{{ t('settings.heading') }}</p>
          <h1>{{ t('settings.subtitle') }}</h1>
          <p class="text-muted">{{ t('settings.description') }}</p>
        </div>
      </div>
    </header>

    <!-- Token Status -->
    <NCard :title="t('settings.auth')" size="medium">
      <NSpace vertical size="medium">
        <div>
          <p><strong>{{ t('settings.auth_status') }}</strong> {{ tokenStatus }}</p>
          <p v-if="bootstrapStore.token"><strong>{{ t('settings.auth_token') }}</strong> <NCode>{{ bootstrapStore.token.substring(0, 20) }}...</NCode></p>
          <p><strong>{{ t('settings.auth_version') }}</strong> {{ bootstrapStore.version || 'Unknown' }}</p>
        </div>

        <NAlert v-if="bootstrapStore.error" type="error" :title="t('settings.auth_error')">
          {{ bootstrapStore.error }}
          <template #action>
            <NButton size="small" @click="refreshToken">{{ t('common.retry') }}</NButton>
          </template>
        </NAlert>

        <NSpace>
          <NButton type="primary" @click="refreshToken" :loading="bootstrapStore.loading">
            {{ t('settings.refresh_token') }}
          </NButton>
          <NButton @click="clearCache">
            {{ t('settings.clear_cache') }}
          </NButton>
        </NSpace>
      </NSpace>
    </NCard>

    <!-- Theme Settings -->
    <NCard :title="t('settings.appearance')" size="medium">
      <NSpace>
        <NButton
          :type="appStore.isDark ? 'default' : 'primary'"
          @click="setTheme('light')"
        >
          {{ t('settings.theme_light') }}
        </NButton>
        <NButton
          :type="appStore.isDark ? 'primary' : 'default'"
          @click="setTheme('dark')"
        >
          {{ t('settings.theme_dark') }}
        </NButton>
        <NButton @click="setTheme('system')">
          {{ t('settings.theme_system') }}
        </NButton>
      </NSpace>
    </NCard>

    <!-- Connection Pool Settings -->
    <NCard :title="t('settings.pool')" size="medium">
      <NSpace vertical size="large">
        <div>
          <p class="text-muted" style="margin-bottom: 16px">
            {{ t('settings.pool_description') }}
          </p>

          <div class="pool-setting">
            <div class="pool-setting-header">
              <label><strong>{{ t('settings.pool_idle_timeout') }}</strong></label>
              <NSwitch v-model:value="useForeverIdle" size="small">
                <template #checked>{{ t('settings.pool_forever') }}</template>
                <template #unchecked>{{ t('settings.pool_custom') }}</template>
              </NSwitch>
            </div>
            <p class="setting-description">{{ t('settings.pool_idle_help') }}</p>
            <NInputNumber
              v-if="!useForeverIdle"
              v-model:value="poolConfig.idle_timeout"
              :min="1"
              :max="1440"
              :step="5"
              style="width: 200px"
              :disabled="poolLoading || poolSaving"
            >
              <template #suffix>{{ t('settings.pool_minutes') }}</template>
            </NInputNumber>
            <div v-else class="forever-badge">
              {{ t('settings.pool_idle_forever') }}
            </div>
          </div>

          <div class="pool-setting">
            <div class="pool-setting-header">
              <label><strong>{{ t('settings.pool_max_lifetime') }}</strong></label>
              <NSwitch v-model:value="useForeverLifetime" size="small">
                <template #checked>{{ t('settings.pool_forever') }}</template>
                <template #unchecked>{{ t('settings.pool_custom') }}</template>
              </NSwitch>
            </div>
            <p class="setting-description">{{ t('settings.pool_lifetime_help') }}</p>
            <NInputNumber
              v-if="!useForeverLifetime"
              v-model:value="poolConfig.max_lifetime"
              :min="1"
              :max="1440"
              :step="5"
              style="width: 200px"
              :disabled="poolLoading || poolSaving"
            >
              <template #suffix>{{ t('settings.pool_minutes') }}</template>
            </NInputNumber>
            <div v-else class="forever-badge">
              {{ t('settings.pool_lifetime_forever') }}
            </div>
          </div>

          <div class="pool-setting">
            <label><strong>{{ t('settings.pool_max_connections') }}</strong></label>
            <p class="setting-description">{{ t('settings.pool_max_connections_help') }}</p>
            <NInputNumber
              v-model:value="poolConfig.max_connections_per_box"
              :min="1"
              :max="10"
              style="width: 200px"
              :disabled="poolLoading || poolSaving"
            >
              <template #suffix>{{ t('settings.pool_connections_unit') }}</template>
            </NInputNumber>
          </div>
        </div>

        <NAlert v-if="poolLoading" type="info">
          {{ t('settings.pool_loading') }}
        </NAlert>

        <NSpace>
          <NButton
            type="primary"
            @click="savePoolConfig"
            :loading="poolSaving"
            :disabled="poolLoading"
          >
            {{ t('settings.pool_save') }}
          </NButton>
          <NButton
            @click="loadPoolConfig"
            :disabled="poolLoading || poolSaving"
          >
            {{ t('common.reset') }}
          </NButton>
        </NSpace>

        <NAlert type="info" :title="t('settings.pool_info')" :bordered="false">
          <p><strong>{{ t('settings.pool_current') }}</strong></p>
          <ul>
            <li>{{ t('settings.pool_current_idle') }}: {{ useForeverIdle ? t('settings.pool_forever') : formatDuration(poolConfig.idle_timeout) }}</li>
            <li>{{ t('settings.pool_current_lifetime') }}: {{ useForeverLifetime ? t('settings.pool_forever') : formatDuration(poolConfig.max_lifetime) }}</li>
            <li>{{ t('settings.pool_current_max') }}: {{ poolConfig.max_connections_per_box }} per box</li>
          </ul>
        </NAlert>
      </NSpace>
    </NCard>

    <!-- Debug Info -->
    <NCard :title="t('settings.debug')" size="medium">
      <NSpace vertical size="small">
        <p><strong>{{ t('settings.debug_url') }}</strong> {{ location.href }}</p>
        <p><strong>{{ t('settings.debug_ua') }}</strong> {{ userAgent }}</p>
        <p><strong>{{ t('settings.debug_ls') }}</strong> {{ localStorageCount }} items</p>
        <p><strong>{{ t('settings.debug_ss') }}</strong> {{ sessionStorageCount }} items</p>
      </NSpace>
    </NCard>

    <!-- Troubleshooting -->
    <NCard :title="t('settings.troubleshooting')" size="medium">
      <NSpace vertical size="medium">
        <div>
          <h3>{{ t('settings.common_issues') }}</h3>
          <ul>
            <li><strong>{{ t('settings.issue_token') }}</strong> {{ t('settings.issue_token_fix') }}</li>
            <li><strong>{{ t('settings.issue_ui') }}</strong> {{ t('settings.issue_ui_fix') }}</li>
            <li><strong>{{ t('settings.issue_terminal') }}</strong> {{ t('settings.issue_terminal_fix') }}</li>
          </ul>
        </div>

        <NAlert type="info" :title="t('settings.need_help')">
          {{ t('settings.help_text') }} <NCode>sshler fix</NCode> {{ t('settings.help_rebuild') }}
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

@media (max-width: 768px) {
  .header-content {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }

  .settings-logo {
    width: 120px;
    max-height: 80px;
  }

  h1 {
    font-size: 22px;
  }

  .pool-setting-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .pool-setting :deep(.n-input-number) {
    width: 100% !important;
  }
}

@media (max-width: 480px) {
  .settings-logo {
    width: 80px;
    max-height: 60px;
  }
}
</style>
