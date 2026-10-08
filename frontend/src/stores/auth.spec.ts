import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { useAuthStore } from './auth'

describe('auth store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  // Mutation killed: changing any initial value (e.g. isAuthenticated true).
  it('starts unauthenticated with an "Unknown" display name', () => {
    const s = useAuthStore()
    expect(s.username).toBeNull()
    expect(s.isAuthenticated).toBe(false)
    expect(s.isBootstrapped).toBe(false)
    expect(s.displayUsername).toBe('Unknown')
  })

  // Mutation killed: setUser not flipping isAuthenticated, or touching isBootstrapped.
  it('setUser records the name and authenticates, leaving bootstrapped alone', () => {
    const s = useAuthStore()
    s.setUser('gabu')
    expect(s.username).toBe('gabu')
    expect(s.isAuthenticated).toBe(true)
    expect(s.isBootstrapped).toBe(false)
    expect(s.displayUsername).toBe('gabu')
  })

  // Mutation killed: clearUser leaving isBootstrapped (or the name) set.
  it('clearUser resets everything including bootstrapped', () => {
    const s = useAuthStore()
    s.setUser('gabu')
    s.setBootstrapped(true)
    s.clearUser()
    expect(s.username).toBeNull()
    expect(s.isAuthenticated).toBe(false)
    expect(s.isBootstrapped).toBe(false)
    expect(s.displayUsername).toBe('Unknown')
  })

  // Mutation killed: setBootstrapped ignoring its argument.
  it('setBootstrapped sets and unsets the flag', () => {
    const s = useAuthStore()
    s.setBootstrapped(true)
    expect(s.isBootstrapped).toBe(true)
    s.setBootstrapped(false)
    expect(s.isBootstrapped).toBe(false)
  })
})
