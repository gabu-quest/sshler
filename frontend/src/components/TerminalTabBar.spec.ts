import { render, fireEvent } from '@testing-library/vue'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick, type App } from 'vue'

import { createI18n } from '@/i18n'
import type { TerminalTab } from '@/stores/terminalTabs'
import TerminalTabBar from './TerminalTabBar.vue'

// ---------------------------------------------------------------------------
// Stubs — keep naive-ui and phosphor icons out of JSDOM
// ---------------------------------------------------------------------------
vi.mock('naive-ui', () => ({
  NIcon: { template: '<span class="stub-icon"><slot /></span>' },
}))

vi.mock('@phosphor-icons/vue', () => ({
  PhX: { template: '<span data-icon="PhX" />' },
  PhPlus: { template: '<span data-icon="PhPlus" />' },
}))

// ---------------------------------------------------------------------------
// i18n plugin — mirrors what other specs in this project do
// ---------------------------------------------------------------------------
const i18nPlugin = {
  install(app: App) {
    createI18n(app)
  },
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const TAB_A: TerminalTab = {
  id: 'a',
  sessionName: 'home',
  directory: '~',
  shell: '',
  label: 'home',
}
const TAB_B: TerminalTab = {
  id: 'b',
  sessionName: 'proj',
  directory: '/srv/proj',
  shell: '',
  label: 'proj',
}

function defaultProps(overrides: Partial<{
  tabs: TerminalTab[]
  activeTabId: string | null
  connectedByTab: Record<string, boolean>
}> = {}) {
  return {
    tabs: [TAB_A, TAB_B],
    activeTabId: 'a',
    connectedByTab: { a: true, b: false },
    ...overrides,
  }
}

function mountBar(props = defaultProps()) {
  return render(TerminalTabBar, {
    props,
    global: { plugins: [i18nPlugin] },
  })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('TerminalTabBar', () => {
  beforeEach(() => {
    // matchMedia stub (naive-ui can query it)
    if (!window.matchMedia) {
      Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        value: (query: string) => ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }),
      })
    }
  })

  // 1. Renders exactly N tabs with correct labels --------------------------
  it('renders exactly N tab items with matching labels', () => {
    const { container } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    expect(items).toHaveLength(2)

    const firstLabel = items[0]!.querySelector('[data-testid="tab-label"]')!
    expect(firstLabel.textContent?.trim()).toBe('home')

    const secondLabel = items[1]!.querySelector('[data-testid="tab-label"]')!
    expect(secondLabel.textContent?.trim()).toBe('proj')
  })

  // 2. Clicking tab emits select with correct id ---------------------------
  it('clicking a tab emits select with the tab id', async () => {
    const { container, emitted } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    await fireEvent.click(items[1]!)
    expect(emitted('select')).toHaveLength(1)
    expect(emitted('select')![0]).toEqual(['b'])
  })

  // 3. Clicking × emits close, NOT select ----------------------------------
  it('clicking the close button emits close with the tab id and does not emit select', async () => {
    const { container, emitted } = mountBar()
    const closeButtons = container.querySelectorAll('[data-testid="tab-close"]')
    await fireEvent.click(closeButtons[0]!)
    expect(emitted('close')).toHaveLength(1)
    expect(emitted('close')![0]).toEqual(['a'])
    expect(emitted('select')).toBeUndefined()
  })

  // 4. Clicking + emits add ------------------------------------------------
  it('clicking + emits add exactly once', async () => {
    const { container, emitted } = mountBar()
    const addBtn = container.querySelector('[data-testid="tab-add"]')!
    await fireEvent.click(addBtn)
    expect(emitted('add')).toHaveLength(1)
  })

  // 5. active class on correct tab -----------------------------------------
  it('active tab has the active class; others do not', () => {
    const { container } = mountBar(defaultProps({ activeTabId: 'b' }))
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    expect(items[0]!.classList.contains('active')).toBe(false)
    expect(items[1]!.classList.contains('active')).toBe(true)
  })

  // 6. Status dot reflects connectedByTab ----------------------------------
  it('connected tab dot has tab-dot--connected class; disconnected tab dot has tab-dot--disconnected', () => {
    const { container } = mountBar(defaultProps({ connectedByTab: { a: true, b: false } }))
    const dots = container.querySelectorAll('[data-testid="tab-dot"]')
    expect(dots).toHaveLength(2)
    // tab a is connected
    expect(dots[0]!.classList.contains('tab-dot--connected')).toBe(true)
    expect(dots[0]!.classList.contains('tab-dot--disconnected')).toBe(false)
    // tab b is disconnected
    expect(dots[1]!.classList.contains('tab-dot--connected')).toBe(false)
    expect(dots[1]!.classList.contains('tab-dot--disconnected')).toBe(true)
  })

  // 7a. Double-click shows rename input ------------------------------------
  it('double-clicking tab label shows the rename input pre-filled with current label', async () => {
    const { container } = mountBar()
    const firstLabel = container.querySelector('[data-testid="tab-label"]')!
    await fireEvent.dblClick(firstLabel)
    await nextTick()
    const input = container.querySelector('[data-testid="tab-rename-input"]') as HTMLInputElement | null
    expect(input).not.toBeNull()
    expect(input!.value).toBe('home')
  })

  // 7b. Enter commits rename emit ------------------------------------------
  it('pressing Enter in rename input emits rename with the new label', async () => {
    const { container, emitted } = mountBar()
    const firstLabel = container.querySelector('[data-testid="tab-label"]')!
    await fireEvent.dblClick(firstLabel)
    await nextTick()
    const input = container.querySelector('[data-testid="tab-rename-input"]') as HTMLInputElement
    await fireEvent.update(input, 'NewLabel')
    await fireEvent.keyUp(input, { key: 'Enter' })
    expect(emitted('rename')).toHaveLength(1)
    expect(emitted('rename')![0]).toEqual(['a', 'NewLabel'])
    // Input should be gone after commit
    await nextTick()
    expect(container.querySelector('[data-testid="tab-rename-input"]')).toBeNull()
  })

  // 7c. Escape cancels rename, no emit -------------------------------------
  it('pressing Escape in rename input cancels and emits nothing', async () => {
    const { container, emitted } = mountBar()
    const firstLabel = container.querySelector('[data-testid="tab-label"]')!
    await fireEvent.dblClick(firstLabel)
    await nextTick()
    const input = container.querySelector('[data-testid="tab-rename-input"]') as HTMLInputElement
    await fireEvent.update(input, 'SomethingElse')
    await fireEvent.keyUp(input, { key: 'Escape' })
    await nextTick()
    expect(emitted('rename')).toBeUndefined()
    expect(container.querySelector('[data-testid="tab-rename-input"]')).toBeNull()
  })

  // 8. Right-click context menu — Kill goes through an are-you-sure modal ---
  // The menu is teleported to <body>, so query the document, not the container.
  it('clicking Kill opens a confirm modal and does NOT emit kill until confirmed', async () => {
    const { container, emitted } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    await fireEvent.contextMenu(items[1]!) // right-click the 'proj' tab (id 'b')
    await nextTick()

    expect(document.querySelector('[data-testid="tab-context-menu"]')).not.toBeNull()
    const killBtn = document.querySelector('[data-testid="tab-menu-kill"]') as HTMLButtonElement
    expect(killBtn).not.toBeNull()
    await fireEvent.click(killBtn)
    await nextTick()

    // Menu closed, confirm modal opened — but nothing killed yet.
    expect(document.querySelector('[data-testid="tab-context-menu"]')).toBeNull()
    const modal = document.querySelector('[data-testid="tab-kill-modal"]')
    expect(modal).not.toBeNull()
    expect(emitted('kill')).toBeUndefined()
    // Confirm message names the tab being killed.
    const body = document.querySelector('[data-testid="tab-kill-modal-body"]')!
    expect(body.textContent).toContain('proj')

    // Confirm — now it emits kill for that exact tab.
    const confirmBtn = document.querySelector('[data-testid="tab-kill-modal-confirm"]') as HTMLButtonElement
    await fireEvent.click(confirmBtn)
    expect(emitted('kill')).toHaveLength(1)
    expect(emitted('kill')![0]).toEqual(['b'])
    // Kill must NOT also close/detach or select the tab.
    expect(emitted('close')).toBeUndefined()
    expect(emitted('select')).toBeUndefined()
    // Modal dismisses after confirming.
    await nextTick()
    expect(document.querySelector('[data-testid="tab-kill-modal"]')).toBeNull()
  })

  it('cancelling the Kill confirm modal emits nothing and dismisses', async () => {
    const { container, emitted } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    await fireEvent.contextMenu(items[1]!)
    await nextTick()
    await fireEvent.click(document.querySelector('[data-testid="tab-menu-kill"]') as HTMLButtonElement)
    await nextTick()
    expect(document.querySelector('[data-testid="tab-kill-modal"]')).not.toBeNull()

    await fireEvent.click(document.querySelector('[data-testid="tab-kill-modal-cancel"]') as HTMLButtonElement)
    await nextTick()
    expect(document.querySelector('[data-testid="tab-kill-modal"]')).toBeNull()
    expect(emitted('kill')).toBeUndefined()
    expect(emitted('close')).toBeUndefined()
  })

  // 8b. Rename via context menu opens a modal that emits rename -------------
  it('clicking Rename opens a modal prefilled with the label; Save emits rename', async () => {
    const { container, emitted } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    await fireEvent.contextMenu(items[1]!) // 'proj' tab (id 'b')
    await nextTick()

    const renameBtn = document.querySelector('[data-testid="tab-menu-rename"]') as HTMLButtonElement
    expect(renameBtn).not.toBeNull()
    await fireEvent.click(renameBtn)
    await nextTick()

    // Menu closed, modal opened with the current label, no emit yet.
    expect(document.querySelector('[data-testid="tab-context-menu"]')).toBeNull()
    const input = document.querySelector('[data-testid="tab-rename-modal-input"]') as HTMLInputElement
    expect(input).not.toBeNull()
    expect(input.value).toBe('proj')
    expect(emitted('rename')).toBeUndefined()

    await fireEvent.update(input, 'renamed-proj')
    await fireEvent.click(document.querySelector('[data-testid="tab-rename-modal-save"]') as HTMLButtonElement)

    expect(emitted('rename')).toHaveLength(1)
    expect(emitted('rename')![0]).toEqual(['b', 'renamed-proj'])
    await nextTick()
    expect(document.querySelector('[data-testid="tab-rename-modal"]')).toBeNull()
  })

  it('rename modal: Enter commits, blank input does not emit, Cancel/Escape dismiss', async () => {
    const { container, emitted } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')

    // Enter commits the new label.
    await fireEvent.contextMenu(items[0]!) // 'home' tab (id 'a')
    await nextTick()
    await fireEvent.click(document.querySelector('[data-testid="tab-menu-rename"]') as HTMLButtonElement)
    await nextTick()
    let input = document.querySelector('[data-testid="tab-rename-modal-input"]') as HTMLInputElement
    await fireEvent.update(input, 'via-enter')
    await fireEvent.keyUp(input, { key: 'Enter' })
    expect(emitted('rename')).toHaveLength(1)
    expect(emitted('rename')![0]).toEqual(['a', 'via-enter'])
    await nextTick()
    expect(document.querySelector('[data-testid="tab-rename-modal"]')).toBeNull()

    // Blank (whitespace) input must NOT emit a rename.
    await fireEvent.contextMenu(items[0]!)
    await nextTick()
    await fireEvent.click(document.querySelector('[data-testid="tab-menu-rename"]') as HTMLButtonElement)
    await nextTick()
    input = document.querySelector('[data-testid="tab-rename-modal-input"]') as HTMLInputElement
    await fireEvent.update(input, '   ')
    await fireEvent.click(document.querySelector('[data-testid="tab-rename-modal-save"]') as HTMLButtonElement)
    expect(emitted('rename')).toHaveLength(1) // still just the one from before
    await nextTick()
    expect(document.querySelector('[data-testid="tab-rename-modal"]')).toBeNull()

    // Escape dismisses without emitting.
    await fireEvent.contextMenu(items[0]!)
    await nextTick()
    await fireEvent.click(document.querySelector('[data-testid="tab-menu-rename"]') as HTMLButtonElement)
    await nextTick()
    input = document.querySelector('[data-testid="tab-rename-modal-input"]') as HTMLInputElement
    await fireEvent.update(input, 'discard-me')
    await fireEvent.keyUp(input, { key: 'Escape' })
    await nextTick()
    expect(document.querySelector('[data-testid="tab-rename-modal"]')).toBeNull()
    expect(emitted('rename')).toHaveLength(1) // unchanged
  })

  it('the context menu Close item emits close (keep the shell running)', async () => {
    const { container, emitted } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    await fireEvent.contextMenu(items[0]!) // right-click the 'home' tab (id 'a')
    await nextTick()

    const closeBtn = document.querySelector('[data-testid="tab-menu-close"]') as HTMLButtonElement
    expect(closeBtn).not.toBeNull()
    await fireEvent.click(closeBtn)

    expect(emitted('close')).toHaveLength(1)
    expect(emitted('close')![0]).toEqual(['a'])
    expect(emitted('kill')).toBeUndefined()
  })

  it('clicking the backdrop closes the menu without emitting kill or close', async () => {
    const { container, emitted } = mountBar()
    const items = container.querySelectorAll('[data-testid="tab-item"]')
    await fireEvent.contextMenu(items[0]!)
    await nextTick()
    expect(document.querySelector('[data-testid="tab-context-menu"]')).not.toBeNull()

    const backdrop = document.querySelector('[data-testid="tab-menu-backdrop"]') as HTMLElement
    await fireEvent.click(backdrop)
    await nextTick()

    expect(document.querySelector('[data-testid="tab-context-menu"]')).toBeNull()
    expect(emitted('kill')).toBeUndefined()
    expect(emitted('close')).toBeUndefined()
  })
})
