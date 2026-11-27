<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NForm, NFormItem, NInput, NButton, NAlert, NIcon } from 'naive-ui'
import { PhLock } from '@phosphor-icons/vue'
import { http } from '@/api/http'
import { ApiError } from '@/api/apiError'
import { useI18n } from '@/i18n'
import { loadPage } from '@/utils/pageLoad'

const { t } = useI18n()
const router = useRouter()

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref<string | null>(null)

/** The line for a lockout: how long to wait when the server said, else the plain line. */
const lockoutLine = (err: ApiError): string => {
  const seconds = err.retryAfter
  if (seconds === null) return t('login.error_rate_limit')
  if (seconds >= 60) return t('login.error_rate_limit_minutes', { minutes: Math.ceil(seconds / 60) })
  return t('login.error_rate_limit_seconds', { seconds: Math.max(1, seconds) })
}

/** Where to go after signing in: a path of this page, never another site. */
const redirectTarget = (): string => {
  const redirect = router.currentRoute.value.query.redirect
  return typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
    ? redirect
    : '/'
}

const handleLogin = async () => {
  error.value = null

  // Basic validation
  if (!username.value || !password.value) {
    error.value = t('login.error_empty')
    return
  }

  loading.value = true

  try {
    // The sign-in route makes the session and sets its cookie.
    await http.post('/api/v1/auth/login', {
      username: username.value,
      password: password.value
    })

    // Load the page afresh at `redirect`: the bootstrap now gives the token, and every
    // socket and store of the page starts signed in.
    loadPage(router.resolve(redirectTarget()).href)
  } catch (err) {
    if (err instanceof ApiError && err.reason === 'auth') {
      error.value = t('login.error_invalid')
    } else if (err instanceof ApiError && err.reason === 'rate_limited') {
      error.value = lockoutLine(err)
    } else if (err instanceof Error && err.message) {
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
          <NIcon size="16" color="var(--muted)" style="vertical-align: middle; margin-right: 4px;">
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
  background: var(--bg);
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
  color: var(--text);
}

.subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 15px;
}

.security-notice {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--stroke);
}

.security-notice p {
  margin: 0 0 8px 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.security-notice p:last-child {
  margin-bottom: 0;
}

.muted-text {
  color: var(--muted) !important;
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
