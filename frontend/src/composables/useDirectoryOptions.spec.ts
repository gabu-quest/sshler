import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useFavoritesStore } from '@/stores/favorites'
import { BROWSE_DIRECTORY, CUSTOM_DIRECTORY, useDirectoryOptions } from './useDirectoryOptions'

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
      { label: '📁 Browse...', value: BROWSE_DIRECTORY },
      { label: '+ Custom path...', value: CUSTOM_DIRECTORY },
    ])
  })

  it('shows no favourites of another box', () => {
    useFavoritesStore().favoritesByBox = new Map([['other', new Set(['/x'])]])
    expect(useDirectoryOptions(() => 'local').value).toHaveLength(3)
  })
})
