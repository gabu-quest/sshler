import { computed, type ComputedRef } from 'vue'

import { useFavoritesStore } from '@/stores/favorites'
import { translate } from '@/i18n'
import { lastPathSegment } from '@/utils/sessionName'

export const BROWSE_DIRECTORY = '__browse__'
export const CUSTOM_DIRECTORY = '__custom__'

// A type alias, not an interface: naive-ui's select options take an open object shape.
export type DirectoryOption = {
  label: string
  value: string
}

/**
 * The directory choices of a terminal form, in this order: home, the box's favourites,
 * Browse, custom path; the three own entries are named in the page's language. The two sentinel values are exported so a view tells them from paths.
 */
export function useDirectoryOptions(box: () => string | null | undefined): ComputedRef<DirectoryOption[]> {
  const favorites = useFavoritesStore()
  return computed(() => {
    const options: DirectoryOption[] = [{ label: translate('terminal.dir_home_option'), value: '~' }]
    const name = box()
    if (name) {
      for (const path of favorites.favoritesForBox(name)) {
        options.push({ label: `★ ${lastPathSegment(path) || path}`, value: path })
      }
    }
    options.push({ label: translate('terminal.dir_browse_option'), value: BROWSE_DIRECTORY })
    options.push({ label: translate('terminal.dir_custom_option'), value: CUSTOM_DIRECTORY })
    return options
  })
}
