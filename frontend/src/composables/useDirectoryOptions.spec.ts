import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// The real translator, not the global English-only stand-in: this spec reads a Japanese page.
vi.mock('@/i18n', async () => await vi.importActual('@/i18n'))

import { createApp } from 'vue'
import { createI18n } from '@/i18n'
import { ja } from '@/locales/ja'
import { useFavoritesStore } from '@/stores/favorites'
import { BROWSE_DIRECTORY, CUSTOM_DIRECTORY, isImeConfirm, useDirectoryOptions } from './useDirectoryOptions'

describe('useDirectoryOptions', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('offers home, Browse and custom when there is no box', () => {
    const options = useDirectoryOptions(() => null)
    expect(options.value.map((o) => o.value)).toEqual(['~', BROWSE_DIRECTORY, CUSTOM_DIRECTORY])
  })

  it('puts the box favourites between home and Browse, labelled by their last folder', () => {
    useFavoritesStore().favoritesByBox = new Map([['local', new Set(['/srv/app', '/home/x/notes/'])]])
    const options = useDirectoryOptions(() => 'local')
    expect(options.value).toEqual([
      { label: '~ (Home)', value: '~' },
      { label: '★ app', value: '/srv/app' },
      { label: '★ notes', value: '/home/x/notes/' },
      { label: 'Browse...', value: BROWSE_DIRECTORY },
      { label: '+ Custom path...', value: CUSTOM_DIRECTORY },
    ])
  })

  // Mutation: the English labels hard-coded -> the Japanese page still reads "Browse...".
  it('names its own entries from the terminal.dir_*_option keys, in the page language', () => {
    localStorage.setItem('sshler:locale', 'ja')
    const app = createApp({})
    createI18n(app)
    try {
      const labels = useDirectoryOptions(() => null).value.map((o) => o.label)
      expect(labels).toEqual([ja['terminal.dir_home_option'], ja['terminal.dir_browse_option'], ja['terminal.dir_custom_option']])
    } finally {
      localStorage.removeItem('sshler:locale')
      createI18n(createApp({}))
    }
  })

  it('shows no favourites of another box', () => {
    useFavoritesStore().favoritesByBox = new Map([['other', new Set(['/x'])]])
    expect(useDirectoryOptions(() => 'local').value).toHaveLength(3)
  })
})

describe('isImeConfirm', () => {
  it.each([
    [{ isComposing: true, keyCode: 13 }, true],
    [{ isComposing: false, keyCode: 229 }, true],
    [{ isComposing: false, keyCode: 13 }, false],
  ])('%j -> %s', (init, expected) => {
    expect(isImeConfirm(new KeyboardEvent('keydown', { key: 'Enter', ...init }))).toBe(expected)
  })
})
