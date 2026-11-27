import { ref, watch, type Ref } from 'vue'

import { gitInfo } from '@/api/http'
import type { GitInfo } from '@/api/types'

/**
 * The git info of a box and folder. Silent on error (the badge is a decoration: its absence is
 * the answer), and an answer for a folder the page has since left is dropped.
 */
export function useGitInfo(
  box: () => string | null | undefined,
  directory: () => string | null | undefined,
  token: () => string | null | undefined,
): { info: Ref<GitInfo | null>; reload: () => Promise<void> } {
  const info = ref<GitInfo | null>(null)
  let latest = 0

  const reload = async () => {
    const request = ++latest
    const name = box()
    const dir = directory()
    if (!name || !dir) {
      info.value = null
      return
    }
    try {
      const result = await gitInfo(name, dir, token() ?? null)
      if (request === latest) info.value = result
    } catch {
      if (request === latest) info.value = null
    }
  }

  watch([box, directory, token], reload, { immediate: true })
  return { info, reload }
}
