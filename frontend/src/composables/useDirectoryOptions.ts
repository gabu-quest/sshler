import { computed, type ComputedRef } from 'vue'

import { useFavoritesStore } from '@/stores/favorites'
import { lastPathSegment } from '@/utils/sessionName'

export const BROWSE_DIRECTORY = '__browse__'
export const CUSTOM_DIRECTORY = '__custom__'

export interface DirectoryOption {
  label: string
  value: string
}

/**
 * The directory choices of a terminal form, in this order: home, the box's favourites,
 * Browse, custom path. The two sentinel values are exported so a view tells them from paths.
 */
export function useDirectoryOptions(box: () => string | null | undefined): ComputedRef<DirectoryOption[]> {
  const favorites = useFavoritesStore()
  return computed(() => {
    const options: DirectoryOption[] = [{ label: '~ (Home)', value: '~' }]
    const name = box()
    if (name) {
      for (const path of favorites.favoritesForBox(name)) {
        options.push({ label: `★ ${lastPathSegment(path) || path}`, value: path })
      }
    }
    options.push({ label: '📁 Browse...', value: BROWSE_DIRECTORY })
    options.push({ label: '+ Custom path...', value: CUSTOM_DIRECTORY })
    return options
  })
}
