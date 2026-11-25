import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

import { http } from '@/api/http'
import { useBootstrapStore } from '@/stores/bootstrap'

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

  /** Ask the server who is signed in (the session cookie's user) and remember it. */
  async function loadUser() {
    const { data } = await http.get<{ username: string }>('/api/v1/auth/me')
    setUser(data.username)
  }

  /**
   * Sign out: the server ends the session and clears its cookie, then the page forgets
   * the user, the bootstrap and the token. A failed sign-out throws and forgets nothing,
   * so the page never shows signed out while the session still lives.
   */
  async function signOut() {
    await http.post('/api/v1/auth/logout')
    clearUser()
    useBootstrapStore().forget()
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
    loadUser,
    signOut,
  }
})
