import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/apiError'
import { useAuthStore } from './auth'
import { useBootstrapStore } from './bootstrap'

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

describe('auth store: who is signed in, and signing out', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    setActivePinia(createPinia())
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function answer(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  it('loadUser remembers the name the server gives for the session', async () => {
    fetchMock.mockResolvedValueOnce(answer(200, { username: 'admin', user_id: 'admin', authenticated: true }))
    const s = useAuthStore()
    await s.loadUser()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/v1/auth/me')
    expect(s.username).toBe('admin')
    expect(s.isAuthenticated).toBe(true)
  })

  it('signOut calls the sign-out route, then forgets the user, the bootstrap and the token', async () => {
    fetchMock.mockResolvedValueOnce(answer(200, { success: true, message: 'Logged out successfully' }))
    const s = useAuthStore()
    const bootstrap = useBootstrapStore()
    s.setUser('admin')
    bootstrap.setToken('tok')
    bootstrap.payload = {
      version: '1', token_header: 'X-SSHLER-TOKEN', token: 'tok', auth_required: true,
      authenticated: true, allow_origins: [], spa_base: '/app/', spa_enabled: true,
    }

    await s.signOut()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/v1/auth/logout')
    expect((fetchMock.mock.calls[0]![1] as RequestInit).method).toBe('POST')
    expect(s.username).toBeNull()
    expect(s.isAuthenticated).toBe(false)
    expect(bootstrap.payload).toBeNull()
    expect(bootstrap.token).toBeNull()
    expect(globalThis.localStorage.getItem('sshler:token')).toBeNull()
  })

  it('a failed signOut throws and forgets nothing', async () => {
    fetchMock.mockResolvedValueOnce(answer(502, { detail: 'proxy down', reason: 'unreachable' }))
    const s = useAuthStore()
    const bootstrap = useBootstrapStore()
    s.setUser('admin')
    bootstrap.setToken('tok')

    const err = await s.signOut().catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).reason).toBe('unreachable')
    expect(s.username).toBe('admin')
    expect(bootstrap.token).toBe('tok')
  })
})
