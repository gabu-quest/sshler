import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// Stub the lazily imported views so navigation does not load real components.
vi.mock('@/views/OverviewView.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/LoginView.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/TerminalView.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/NotFoundView.vue', () => ({ default: { template: '<div />' } }))

const bootstrapMock = vi.hoisted(() => ({
  state: {
    token: null as string | null,
    loading: false,
    payload: null as object | null,
    authRequired: false,
    authenticated: false,
    bootstrap: vi.fn(async () => {}),
  },
  throwOnUse: false,
}))
const authMock = vi.hoisted(() => ({
  state: { isAuthenticated: false, loadUser: vi.fn(async () => {}) },
}))

vi.mock('@/stores/bootstrap', () => ({
  useBootstrapStore: () => {
    if (bootstrapMock.throwOnUse) throw new Error('boom')
    return bootstrapMock.state
  },
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => authMock.state }))

import router from './index'

async function resetTo(path: string) {
  // Navigate to a public route first so each test starts from a known location.
  await router.push('/login')
  bootstrapMock.state.bootstrap.mockClear()
  if (path !== '/login') await router.push(path).catch(() => {})
}

describe('router guards', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    bootstrapMock.state.token = 'tok'
    bootstrapMock.state.loading = false
    bootstrapMock.state.payload = {}
    bootstrapMock.state.authRequired = false
    bootstrapMock.state.authenticated = false
    bootstrapMock.state.bootstrap.mockClear()
    bootstrapMock.throwOnUse = false
    authMock.state.isAuthenticated = false
    authMock.state.loadUser.mockClear()
    document.head.innerHTML = ''
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })

  // Mutation killed: title format changed, or meta description not written.
  it('sets the document title and creates the meta description', async () => {
    await router.push('/')
    expect(document.title).toBe('Overview - sshler')
    expect(
      document.head.querySelector('meta[name="description"]')?.getAttribute('content'),
    ).toBe('SSH server overview and quick actions')
  })

  // Mutation killed: appending a second <meta> instead of updating the existing one.
  it('reuses the existing meta description element', async () => {
    await router.push('/')
    await router.push('/terminal')
    const metas = document.head.querySelectorAll('meta[name="description"]')
    expect(metas).toHaveLength(1)
    expect(metas[0]?.getAttribute('content')).toBe('Access remote terminal sessions')
    expect(document.title).toBe('Terminal - sshler')
  })

  // Mutation killed: not-found route losing its meta / catch-all path.
  it('unknown paths resolve to the not-found route', async () => {
    await router.push('/definitely/not/here')
    expect(router.currentRoute.value.name).toBe('not-found')
    expect(document.title).toBe('Page Not Found - sshler')
  })

  // Mutation killed: redirecting when no password is set.
  it('lets everyone through when no password is set', async () => {
    await router.push('/terminal')
    expect(router.currentRoute.value.name).toBe('terminal')
  })

  // Mutation killed: dropping the redirect query or changing the target route name.
  it('redirects unauthenticated users to login carrying the full path', async () => {
    bootstrapMock.state.authRequired = true
    await router.push('/terminal?box=web')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query).toEqual({ redirect: '/terminal?box=web' })
  })

  // Mutation killed: the guard deciding by the in-memory auth store, which a reload empties.
  it('lets a page the bootstrap calls signed in through, and loads the user once', async () => {
    bootstrapMock.state.authRequired = true
    bootstrapMock.state.authenticated = true
    await router.push('/terminal')
    expect(router.currentRoute.value.name).toBe('terminal')
    expect(authMock.state.loadUser).toHaveBeenCalledTimes(1)

    authMock.state.isAuthenticated = true
    await router.push('/')
    expect(authMock.state.loadUser).toHaveBeenCalledTimes(1)
  })

  // Mutation killed: trusting the in-memory auth store over the bootstrap.
  it('sends a page the bootstrap calls signed out to login even if the store says signed in', async () => {
    bootstrapMock.state.authRequired = true
    bootstrapMock.state.authenticated = false
    authMock.state.isAuthenticated = true
    await router.push('/terminal')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query).toEqual({ redirect: '/terminal' })
  })

  // Mutation killed: guarding the login route too (redirect loop) or consulting bootstrap for it.
  it('never gates or bootstraps for the public login route', async () => {
    await router.push('/')
    bootstrapMock.state.authRequired = true
    bootstrapMock.state.payload = null
    bootstrapMock.state.token = null
    bootstrapMock.state.bootstrap.mockClear()
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('login')
    expect(bootstrapMock.state.bootstrap).not.toHaveBeenCalled()
  })

  // Mutation killed: skipping the bootstrap because a token is stored (a reload kept
  // the token of a session that has since ended), or asking it again on every navigation.
  it('asks the bootstrap whenever the page has none, a stored token or not', async () => {
    await resetTo('/login')
    bootstrapMock.state.payload = null
    bootstrapMock.state.token = 'tok'
    await router.push('/')
    expect(bootstrapMock.state.bootstrap).toHaveBeenCalledTimes(1)

    bootstrapMock.state.bootstrap.mockClear()
    bootstrapMock.state.payload = {}
    bootstrapMock.state.token = null
    await router.push('/terminal')
    expect(bootstrapMock.state.bootstrap).not.toHaveBeenCalled()
  })

  // Mutation killed: removing the try/catch (navigation would reject) or failing closed.
  it('fails open when the auth check throws', async () => {
    await resetTo('/login')
    bootstrapMock.throwOnUse = true
    await router.push('/terminal')
    expect(router.currentRoute.value.name).toBe('terminal')
  })
})
