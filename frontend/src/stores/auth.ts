import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  // State - NEVER store credentials or reusable secrets
  const username = ref<string | null>(null)
  const isAuthenticated = ref(false)
  const isBootstrapped = ref(false)

  // Computed
  const displayUsername = computed(() => username.value || 'Unknown')

  // Actions
  function setUser(user: string) {
    username.value = user
    isAuthenticated.value = true
  }

  function clearUser() {
    username.value = null
    isAuthenticated.value = false
    isBootstrapped.value = false
  }

  function setBootstrapped(value: boolean) {
    isBootstrapped.value = value
  }

  return {
    // State
    username,
    isAuthenticated,
    isBootstrapped,

    // Computed
    displayUsername,

    // Actions
    setUser,
    clearUser,
    setBootstrapped,
  }
})
