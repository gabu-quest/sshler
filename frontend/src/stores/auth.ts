import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  // State
  const username = ref<string | null>(null)
  const authHeader = ref<string | null>(null)
  const isAuthenticated = ref(false)

  // Computed
  const displayUsername = computed(() => username.value || 'Unknown')

  // Actions
  function setCredentials(user: string, password: string) {
    username.value = user

    // Generate Basic Auth header
    const credentials = `${user}:${password}`
    const encoded = btoa(credentials)
    authHeader.value = `Basic ${encoded}`

    // Store in sessionStorage (NOT localStorage for security)
    // sessionStorage is cleared when browser tab closes
    sessionStorage.setItem('sshler:auth:username', user)
    sessionStorage.setItem('sshler:auth:header', authHeader.value)

    isAuthenticated.value = true
  }

  function clearCredentials() {
    username.value = null
    authHeader.value = null
    isAuthenticated.value = false

    // Clear from sessionStorage
    sessionStorage.removeItem('sshler:auth:username')
    sessionStorage.removeItem('sshler:auth:header')
  }

  function loadStoredAuth(): boolean {
    // Try to load from sessionStorage
    const storedUsername = sessionStorage.getItem('sshler:auth:username')
    const storedHeader = sessionStorage.getItem('sshler:auth:header')

    if (storedUsername && storedHeader) {
      username.value = storedUsername
      authHeader.value = storedHeader
      isAuthenticated.value = true
      return true
    }

    return false
  }

  function getAuthHeader(): string | null {
    return authHeader.value
  }

  // Initialize on store creation
  loadStoredAuth()

  return {
    // State
    username,
    authHeader,
    isAuthenticated,

    // Computed
    displayUsername,

    // Actions
    setCredentials,
    clearCredentials,
    loadStoredAuth,
    getAuthHeader,
  }
})
