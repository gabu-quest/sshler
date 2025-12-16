<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NForm, NFormItem, NInput, NButton, NAlert, NSpace } from 'naive-ui'
import { LockClosedOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useBootstrapStore } from '@/stores/bootstrap'

const router = useRouter()
const authStore = useAuthStore()
const bootstrapStore = useBootstrapStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref<string | null>(null)

const handleLogin = async () => {
  error.value = null

  // Basic validation
  if (!username.value || !password.value) {
    error.value = 'Please enter both username and password'
    return
  }

  loading.value = true

  try {
    // Set credentials in auth store
    authStore.setCredentials(username.value, password.value)

    // Test the credentials by refreshing bootstrap
    // This will fail with 401 if credentials are invalid
    await bootstrapStore.refreshToken()

    // If we got here, auth was successful
    // Redirect to the page they were trying to access or home
    const redirect = router.currentRoute.value.query.redirect as string || '/'
    router.push(redirect)
  } catch (err) {
    // Clear invalid credentials
    authStore.clearCredentials()

    if (err && typeof err === 'object' && 'response' in err) {
      const response = (err as any).response
      if (response?.status === 401) {
        error.value = 'Invalid username or password'
      } else if (response?.status === 429) {
        const retryAfter = response.headers?.['retry-after'] || 'a few minutes'
        error.value = `Too many failed attempts. Please try again in ${retryAfter} seconds`
      } else {
        error.value = 'Authentication failed. Please try again.'
      }
    } else {
      error.value = 'Unable to connect to server. Please check your connection.'
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
        <NIcon size="48" color="var(--primary-color)">
          <LockClosedOutline />
        </NIcon>
        <h1>Authentication Required</h1>
        <p class="subtitle">Please sign in to access sshler</p>
      </div>

      <NCard>
        <NForm>
          <NFormItem label="Username" :show-require-mark="false">
            <NInput
              v-model:value="username"
              placeholder="Enter username"
              size="large"
              :disabled="loading"
              @keypress="handleKeypress"
              autofocus
            />
          </NFormItem>

          <NFormItem label="Password" :show-require-mark="false">
            <NInput
              v-model:value="password"
              type="password"
              placeholder="Enter password"
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
            Sign In
          </NButton>
        </NForm>

        <div class="security-notice">
          <p>
            <strong>Security Notice:</strong> sshler uses Argon2id password hashing
            and enforces strict authentication. Your credentials are never stored in plaintext.
          </p>
          <p class="muted-text">
            Session expires when browser tab closes for your security.
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
</style>
