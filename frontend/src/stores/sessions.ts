import { ref } from 'vue'
import { defineStore } from 'pinia'
import { syncBoxSessions as apiSyncBoxSessions } from '@/api/http'
import type { ApiSession } from '@/api/types'

export const useSessionsStore = defineStore('sessions', () => {
  // Map of box name -> sessions array
  const sessionsByBox = ref<Map<string, ApiSession[]>>(new Map())

  /**
   * Sync sessions for a box with actual tmux state
   * Marks stale DB sessions as inactive
   */
  async function syncBoxSessions(boxName: string, token: string | null): Promise<ApiSession[]> {
    try {
      const sessions = await apiSyncBoxSessions(boxName, token)
      sessionsByBox.value.set(boxName, sessions)
      return sessions
    } catch (e) {
      // Silent on purpose: the overview asks again in a moment, and the box keeps the
      // sessions it last had meanwhile.
      console.warn(`Failed to sync sessions for ${boxName}:`, e)
      return sessionsByBox.value.get(boxName) || []
    }
  }

  /**
   * Sync sessions for all boxes in parallel
   */
  async function syncAllBoxSessions(boxNames: string[], token: string | null): Promise<void> {
    // allSettled so one unreachable box doesn't block or fail the entire batch
    await Promise.allSettled(
      boxNames.map(name => syncBoxSessions(name, token))
    )
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
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto', style: 'narrow' })

    if (diff < 60) return rtf.format(0, 'second')
    if (diff < 3600) return rtf.format(-Math.floor(diff / 60), 'minute')
    if (diff < 86400) return rtf.format(-Math.floor(diff / 3600), 'hour')
    if (diff < 604800) return rtf.format(-Math.floor(diff / 86400), 'day')
    return rtf.format(-Math.floor(diff / 604800), 'week')
  }

  function $reset() {
    sessionsByBox.value = new Map()
  }

  return {
    sessionsByBox,
    syncBoxSessions,
    syncAllBoxSessions,
    getBoxSessions,
    getActiveSessionCount,
    getMostRecentSession,
    formatRelativeTime,
    $reset
  }
})
