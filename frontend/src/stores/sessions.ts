import { ref } from 'vue'
import { defineStore } from 'pinia'
import { buildHeaders } from '@/api/http'
import type { ApiSession } from '@/api/types'

export const useSessionsStore = defineStore('sessions', () => {
  // Map of box name -> sessions array
  const sessionsByBox = ref<Map<string, ApiSession[]>>(new Map())
  const loading = ref(false)
  const error = ref<string | null>(null)

  /**
   * Load sessions for a specific box
   */
  async function loadBoxSessions(boxName: string, token: string | null, activeOnly = false): Promise<ApiSession[]> {
    try {
      const params = new URLSearchParams()
      if (activeOnly) params.set('active_only', 'true')
      
      const url = `/api/v1/boxes/${encodeURIComponent(boxName)}/sessions${params.toString() ? '?' + params.toString() : ''}`
      const response = await fetch(url, {
        headers: buildHeaders(token),
        credentials: 'include'
      })
      
      if (!response.ok) {
        throw new Error(`Failed to load sessions: ${response.status}`)
      }
      
      const sessions = await response.json() as ApiSession[]
      sessionsByBox.value.set(boxName, sessions)
      return sessions
    } catch (e) {
      console.warn(`Failed to load sessions for ${boxName}:`, e)
      sessionsByBox.value.set(boxName, [])
      return []
    }
  }

  /**
   * Load sessions for all boxes in parallel
   */
  async function loadAllBoxSessions(boxNames: string[], token: string | null, activeOnly = false): Promise<void> {
    loading.value = true
    error.value = null

    try {
      await Promise.all(
        boxNames.map(name => loadBoxSessions(name, token, activeOnly))
      )
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load sessions'
    } finally {
      loading.value = false
    }
  }

  /**
   * Sync sessions for a box with actual tmux state
   * Marks stale DB sessions as inactive
   */
  async function syncBoxSessions(boxName: string, token: string | null): Promise<ApiSession[]> {
    try {
      const url = `/api/v1/boxes/${encodeURIComponent(boxName)}/sessions/sync`
      const response = await fetch(url, {
        method: 'POST',
        headers: buildHeaders(token),
        credentials: 'include'
      })

      if (!response.ok) {
        throw new Error(`Failed to sync sessions: ${response.status}`)
      }

      const sessions = await response.json() as ApiSession[]
      sessionsByBox.value.set(boxName, sessions)
      return sessions
    } catch (e) {
      console.warn(`Failed to sync sessions for ${boxName}:`, e)
      return sessionsByBox.value.get(boxName) || []
    }
  }

  /**
   * Sync sessions for all boxes in parallel
   */
  async function syncAllBoxSessions(boxNames: string[], token: string | null): Promise<void> {
    loading.value = true
    error.value = null

    try {
      await Promise.all(
        boxNames.map(name => syncBoxSessions(name, token))
      )
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to sync sessions'
    } finally {
      loading.value = false
    }
  }

  /**
   * Get sessions for a specific box
   */
  function getBoxSessions(boxName: string): ApiSession[] {
    return sessionsByBox.value.get(boxName) || []
  }

  /**
   * Get active session count for a box
   */
  function getActiveSessionCount(boxName: string): number {
    const sessions = sessionsByBox.value.get(boxName) || []
    return sessions.filter(s => s.active).length
  }

  /**
   * Get the most recent session for a box
   */
  function getMostRecentSession(boxName: string): ApiSession | null {
    const sessions = sessionsByBox.value.get(boxName) || []
    if (sessions.length === 0) return null
    
    return sessions.reduce((latest, session) => 
      session.last_accessed_at > latest.last_accessed_at ? session : latest
    )
  }

  /**
   * Format relative time from timestamp
   */
  function formatRelativeTime(timestamp: number): string {
    const now = Date.now() / 1000
    const diff = now - timestamp
    
    if (diff < 60) return 'just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}min ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}hr ago`
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
    return `${Math.floor(diff / 604800)}w ago`
  }

  function $reset() {
    sessionsByBox.value = new Map()
    loading.value = false
    error.value = null
  }

  return {
    sessionsByBox,
    loading,
    error,
    loadBoxSessions,
    loadAllBoxSessions,
    syncBoxSessions,
    syncAllBoxSessions,
    getBoxSessions,
    getActiveSessionCount,
    getMostRecentSession,
    formatRelativeTime,
    $reset
  }
})
