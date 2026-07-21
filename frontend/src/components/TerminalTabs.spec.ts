import { render } from '@testing-library/vue'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

import TerminalTabs from './TerminalTabs.vue'
import { useTerminalTabsStore, scopeKey } from '@/stores/terminalTabs'
import { killTerminalSession } from '@/api/http'

// TerminalTabs is the only http consumer in this graph (it calls
// killTerminalSession for the "kill terminal" action); stub the module so the
// test never hits the network.
vi.mock('@/api/http', () => ({
  killTerminalSession: vi.fn(() => Promise.resolve({ status: 'ok' })),
}))
const killSpy = vi.mocked(killTerminalSession)

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

// ---------------------------------------------------------------------------
// Stub the heavy <Terminal> (xterm) child. It records the props it receives on
// data-attributes so the test can assert what TerminalTabs passes down.
// (Defined inside the factory because vi.mock is hoisted above module scope.)
// ---------------------------------------------------------------------------
vi.mock('@/components/Terminal.vue', async () => {
  const { defineComponent } = await import('vue')
  return {
    default: defineComponent({
      name: 'Terminal',
      props: {
        boxName: { type: String, default: '' },
        sessionName: { type: String, default: '' },
        directory: { type: String, default: '' },
        shell: { type: String, default: '' },
        nativeShell: { type: Boolean, default: undefined },
        theme: { type: String, default: '' },
        fontSize: { type: Number, default: 14 },
        fontFamily: { type: String, default: '' },
        showTitleBar: { type: Boolean, default: true },
        externalInput: { type: Boolean, default: false },
      },
      emits: ['connected', 'disconnected'],
      // TerminalTabs drives the active ref via these — stub them as no-ops.
      methods: {
        fit() {},
        focus() {},
        send() {},
        sendRaw() {},
      },
      // String(undefined) === 'undefined' — distinguishes "never passed" from false.
      template:
        '<div class="term-stub" :data-native-shell="String(nativeShell)" :data-shell="shell" ' +
        ':data-session="sessionName" :data-directory="directory" />',
    }),
  }
})

// Keep TerminalTabBar lightweight — it pulls in naive-ui/phosphor otherwise.
vi.mock('./TerminalTabBar.vue', async () => {
  const { defineComponent } = await import('vue')
  return {
    default: defineComponent({ name: 'TerminalTabBar', template: '<div class="tab-bar-stub" />' }),
  }
})

describe('TerminalTabs', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    killSpy.mockClear()
  })

  // -------------------------------------------------------------------------
  // Regression (tmux injection): a Windows-local box ALWAYS runs a native
  // ConPTY shell, even when the chosen shell id is empty (backend falls back to
  // the default shell). The terminal must therefore be marked nativeShell=true
  // so it never injects tmux control sequences (^B, `set -g mouse on`) into the
  // ConPTY. Before the fix, isNativeShell derived from `!!props.shell`, so an
  // empty-shell tab was treated as tmux and spewed garbage into PowerShell.
  // -------------------------------------------------------------------------
  it('marks every terminal nativeShell=true even when the shell id is empty', async () => {
    const { container } = render(TerminalTabs, {
      props: {
        boxName: 'local',
        directory: 'C:\\Users\\me\\proj',
        seedShell: '', // empty shell id — still a native Windows ConPTY
      },
      global: { plugins: [createPinia()] },
    })
    await nextTick()

    const stubs = container.querySelectorAll('.term-stub')
    expect(stubs).toHaveLength(1)
    const term = stubs[0]!
    expect(term.getAttribute('data-shell')).toBe('')
    expect(term.getAttribute('data-native-shell')).toBe('true')
  })

  it('keeps nativeShell=true for an explicitly chosen shell too', async () => {
    const { container } = render(TerminalTabs, {
      props: {
        boxName: 'local',
        directory: 'C:\\Users\\me\\proj',
        seedShell: 'pwsh',
      },
      global: { plugins: [createPinia()] },
    })
    await nextTick()

    const term = container.querySelector('.term-stub')!
    expect(term.getAttribute('data-shell')).toBe('pwsh')
    expect(term.getAttribute('data-native-shell')).toBe('true')
  })

  // -------------------------------------------------------------------------
  // Regression (per-directory scoping / no warp): tabs are scoped per
  // (box, directory). Navigating to a different directory must swap the strip
  // to THAT directory's own tab set — not strand the user in a leftover tab
  // from the previous directory. The seeded tab's session name derives from the
  // directory, so it doubles as a scope marker.
  // -------------------------------------------------------------------------
  it('swaps to the new directory\'s tab set when the directory changes', async () => {
    const { container, rerender } = render(TerminalTabs, {
      props: { boxName: 'local', directory: 'C:\\work\\alpha', seedShell: 'pwsh' },
      global: { plugins: [createPinia()] },
    })
    await nextTick()

    let stubs = container.querySelectorAll('.term-stub')
    expect(stubs).toHaveLength(1)
    expect(stubs[0]!.getAttribute('data-session')).toBe('alpha')
    expect(stubs[0]!.getAttribute('data-directory')).toBe('C:\\work\\alpha')

    // Navigate to a different directory — must show beta's scope, not alpha's.
    await rerender({ boxName: 'local', directory: 'C:\\work\\beta', seedShell: 'pwsh' })
    await nextTick()

    stubs = container.querySelectorAll('.term-stub')
    expect(stubs).toHaveLength(1)
    expect(stubs[0]!.getAttribute('data-session')).toBe('beta')
    expect(stubs[0]!.getAttribute('data-directory')).toBe('C:\\work\\beta')
  })

  // -------------------------------------------------------------------------
  // Regression (tab-switch squish): background panes must stay rendered and be
  // hidden by a class toggle (visibility), NOT removed/collapsed via v-show.
  // A terminal first laid out at display:none measures a 0 cell and can never
  // be fit on reveal — it stays ~20 cols wide. Keeping every pane laid out is
  // what lets FitAddon size a tab correctly the first time it's shown.
  // -------------------------------------------------------------------------
  it('keeps every pane rendered and toggles active via the tab-pane--active class', async () => {
    const pinia = createPinia()
    const { container } = render(TerminalTabs, {
      props: { boxName: 'local', directory: 'C:\\work\\alpha', seedShell: 'pwsh' },
      global: { plugins: [pinia] },
    })
    await nextTick()

    // Add a second tab in the SAME scope, then make the first one active again.
    setActivePinia(pinia)
    const store = useTerminalTabsStore()
    const scope = scopeKey('local', 'C:\\work\\alpha')
    const first = store.tabs(scope)[0]!
    store.addTab(scope, { directory: 'C:\\work\\alpha', shell: 'pwsh' }) // becomes active
    await nextTick()
    store.activateTab(scope, first.id)
    await nextTick()

    // BOTH panes remain in the DOM (background pane is not removed/v-show'd).
    const panes = Array.from(container.querySelectorAll('.term-stub'))
    expect(panes).toHaveLength(2)

    // Exactly one is marked active via the class — the rest are merely hidden.
    const active = panes.filter((p) => p.classList.contains('tab-pane--active'))
    expect(active).toHaveLength(1)
    expect(panes.every((p) => p.classList.contains('tab-pane'))).toBe(true)
  })

  // -------------------------------------------------------------------------
  // Kill terminal for good: Alt+Shift+W (and the tab-bar Kill menu) must
  // TERMINATE the server-side shell, not just detach. Otherwise reopening the
  // same directory re-attaches to the old (laggy) shell — the reported bug.
  // The kill removes the tab locally only AFTER the backend kill resolves.
  // -------------------------------------------------------------------------
  it('Alt+Shift+W terminates the active tab\'s shell and removes it locally', async () => {
    const pinia = createPinia()
    render(TerminalTabs, {
      props: { boxName: 'local', directory: 'C:\\work\\alpha', seedShell: 'pwsh', token: 'tok' },
      global: { plugins: [pinia] },
    })
    await nextTick()

    setActivePinia(pinia)
    const store = useTerminalTabsStore()
    const scope = scopeKey('local', 'C:\\work\\alpha')
    const first = store.tabs(scope)[0]!
    // A second tab so the kill leaves a survivor (no auto-reseed to confuse us).
    store.addTab(scope, { directory: 'C:\\work\\alpha', shell: 'pwsh' })
    const second = store.tabs(scope)[1]!
    store.activateTab(scope, first.id) // first is the active (to-be-killed) tab
    await nextTick()

    window.dispatchEvent(
      new KeyboardEvent('keydown', { code: 'KeyW', altKey: true, shiftKey: true }),
    )
    await flush()

    // The backend was asked to kill exactly this box+session with the token.
    expect(killSpy).toHaveBeenCalledTimes(1)
    expect(killSpy).toHaveBeenCalledWith('local', first.sessionName, 'tok')

    // The killed tab is gone; the other survives.
    const remaining = store.tabs(scope)
    expect(remaining).toHaveLength(1)
    expect(remaining[0]!.id).toBe(second.id)
  })

  it('plain Alt+W does NOT kill the shell (detach only — no backend call)', async () => {
    const pinia = createPinia()
    render(TerminalTabs, {
      props: { boxName: 'local', directory: 'C:\\work\\alpha', seedShell: 'pwsh', token: 'tok' },
      global: { plugins: [pinia] },
    })
    await nextTick()

    setActivePinia(pinia)
    const store = useTerminalTabsStore()
    const scope = scopeKey('local', 'C:\\work\\alpha')
    store.addTab(scope, { directory: 'C:\\work\\alpha', shell: 'pwsh' })
    const first = store.tabs(scope)[0]!
    store.activateTab(scope, first.id)
    await nextTick()

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW', altKey: true }))
    await flush()

    expect(killSpy).not.toHaveBeenCalled()
    // Tab is still detached/removed locally (length back to 1), but no kill.
    expect(store.tabs(scope)).toHaveLength(1)
  })

  it('restores the original directory\'s tabs when navigating back', async () => {
    const { container, rerender } = render(TerminalTabs, {
      props: { boxName: 'local', directory: 'C:\\work\\alpha', seedShell: 'pwsh' },
      global: { plugins: [createPinia()] },
    })
    await nextTick()
    const alphaSession = container.querySelector('.term-stub')!.getAttribute('data-session')
    expect(alphaSession).toBe('alpha')

    await rerender({ boxName: 'local', directory: 'C:\\work\\beta', seedShell: 'pwsh' })
    await nextTick()
    expect(container.querySelector('.term-stub')!.getAttribute('data-session')).toBe('beta')

    // Back to alpha — alpha's persisted scope must come back, not a fresh seed.
    await rerender({ boxName: 'local', directory: 'C:\\work\\alpha', seedShell: 'pwsh' })
    await nextTick()
    expect(container.querySelector('.term-stub')!.getAttribute('data-session')).toBe('alpha')
  })
})
