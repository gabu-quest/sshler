<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NForm, NFormItem, NInput, NButton, NAlert, NIcon } from 'naive-ui'
import { PhLock } from '@phosphor-icons/vue'
import { useAuthStore } from '@/stores/auth'
import { http } from '@/api/http'
import { useI18n } from '@/i18n'

const { t } = useI18n()
const router = useRouter()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref<string | null>(null)

const handleLogin = async () => {
  error.value = null

  // Basic validation
  if (!username.value || !password.value) {
    error.value = t('login.error_empty')
    return
  }

  loading.value = true

  try {
    // Call /auth/login endpoint
    await http.post('/api/v1/auth/login', {
      username: username.value,
      password: password.value
    })

    // On success, fetch user info
    const { data: userInfo } = await http.get('/api/v1/auth/me')

    // Update auth store
    authStore.setUser(userInfo.username)
    authStore.setBootstrapped(true)

    // Redirect to the page they were trying to access or home
    const redirect = router.currentRoute.value.query.redirect as string || '/'
    router.push(redirect)
  } catch (err: any) {
    // Handle errors
    if (err?.response?.status === 401) {
      error.value = t('login.error_invalid')
    } else if (err?.response?.status === 429) {
      const retryAfter = err.response?.headers?.['retry-after'] || 'a few minutes'
      error.value = t('login.error_rate_limit', { seconds: retryAfter })
    } else if (err?.message) {
      error.value = err.message
    } else {
      error.value = t('login.error_generic')
    }
  } finally {
    loading.value = false
  }
}

const handleKeypress = (event: KeyboardEvent) => {
  if (event.key === 'Enter') {
    handleLogin()
  }
}
</script>

<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-header">
        <img src="/logo.png" alt="sshler" class="login-logo" />
        <p class="subtitle">
          <NIcon size="16" color="var(--text-color-3)" style="vertical-align: middle; margin-right: 4px;">
            <PhLock weight="duotone" />
          </NIcon>
          {{ t('login.auth_required') }}
        </p>
      </div>

      <NCard>
        <NForm>
          <NFormItem :label="t('login.username')" :show-require-mark="false">
            <NInput
              v-model:value="username"
              :placeholder="t('login.username_placeholder')"
              size="large"
              :disabled="loading"
              @keypress="handleKeypress"
              autofocus
            />
          </NFormItem>

          <NFormItem :label="t('login.password')" :show-require-mark="false">
            <NInput
              v-model:value="password"
              type="password"
              :placeholder="t('login.password_placeholder')"
              size="large"
              show-password-on="click"
              :disabled="loading"
              @keypress="handleKeypress"
            />
          </NFormItem>

          <NAlert v-if="error" type="error" :bordered="false" style="margin-bottom: 16px">
            {{ error }}
          </NAlert>

          <NButton
            type="primary"
            size="large"
            block
            :loading="loading"
            @click="handleLogin"
          >
            {{ t('login.sign_in') }}
          </NButton>
        </NForm>

        <div class="security-notice">
          <p>
            <strong>{{ t('login.security_notice') }}</strong> {{ t('login.security_detail') }}
          </p>
          <p class="muted-text">
            {{ t('login.session_expiry') }}
          </p>
        </div>
      </NCard>
    </div>
  </div>
</template>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--body-color);
  padding: 24px;
}

.login-box {
  width: 100%;
  max-width: 420px;
}

.login-header {
  text-align: center;
  margin-bottom: 24px;
}

.login-logo {
  width: 120px;
  height: 120px;
  object-fit: contain;
  border-radius: 16px;
  box-shadow: 0 4px 24px var(--shadow-lg);
}

.login-header h1 {
  margin: 16px 0 8px 0;
  font-size: 28px;
  font-weight: 600;
  color: var(--text-color);
}

.subtitle {
  margin: 0;
  color: var(--text-color-3);
  font-size: 15px;
}

.security-notice {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--divider-color);
}

.security-notice p {
  margin: 0 0 8px 0;
  font-size: 13px;
  color: var(--text-color-2);
  line-height: 1.6;
}

.security-notice p:last-child {
  margin-bottom: 0;
}

.muted-text {
  color: var(--text-color-3) !important;
  font-size: 12px !important;
}

.n-form-item {
  margin-bottom: 20px;
}

@media (max-width: 480px) {
  .login-container {
    padding: 16px;
  }

  .login-logo {
    width: 80px;
    height: 80px;
  }

  .login-header {
    margin-bottom: 16px;
  }
}
</style>
