import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const gitInfo = vi.hoisted(() => vi.fn())
vi.mock('@/api/http', () => ({ gitInfo }))

import { useGitInfo } from './useGitInfo'

const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => ((resolve = res), (reject = rej)))
  return { promise, resolve, reject }
}
const flush = async () => {
  await nextTick()
  await Promise.resolve()
  await Promise.resolve()
}

describe('useGitInfo', () => {
  beforeEach(() => gitInfo.mockReset())

  it('loads for a box and folder and stays empty without either', async () => {
    gitInfo.mockResolvedValue({ branch: 'main' })
    const dir = ref('')
    const { info } = useGitInfo(() => 'local', () => dir.value, () => 't')
    await flush()
    expect(gitInfo).not.toHaveBeenCalled()
    dir.value = '/srv'
    await flush()
    expect(gitInfo).toHaveBeenCalledWith('local', '/srv', 't')
    expect(info.value).toEqual({ branch: 'main' })
  })

  it('loads again when the token arrives after the first load', async () => {
    gitInfo.mockResolvedValueOnce(null).mockResolvedValueOnce({ branch: 'main' })
    const token = ref<string | null>(null)
    const { info } = useGitInfo(() => 'local', () => '/srv', () => token.value)
    await flush()
    expect(gitInfo).toHaveBeenCalledTimes(1)
    token.value = 'late'
    await flush()
    expect(gitInfo).toHaveBeenLastCalledWith('local', '/srv', 'late')
    expect(info.value).toEqual({ branch: 'main' })
  })

  it('is silent on an error: the info is null and nothing throws', async () => {
    const failing = deferred<unknown>()
    gitInfo.mockReturnValueOnce(failing.promise)
    const { info } = useGitInfo(() => 'local', () => '/srv', () => null)
    await nextTick()
    failing.reject(new Error('500'))
    await flush()
    expect(info.value).toBeNull()
  })

  it('shows only the last answer after two quick folder changes', async () => {
    const first = deferred<unknown>()
    const second = deferred<unknown>()
    gitInfo.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const dir = ref('/a')
    const { info } = useGitInfo(() => 'local', () => dir.value, () => null)
    await nextTick()
    dir.value = '/b'
    await nextTick()
    second.resolve({ branch: 'b' })
    await flush()
    first.resolve({ branch: 'a' })
    await flush()
    expect(info.value).toEqual({ branch: 'b' })
  })

  it('drops a stale error as well', async () => {
    const first = deferred<unknown>()
    gitInfo.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ branch: 'b' })
    const dir = ref('/a')
    const { info } = useGitInfo(() => 'local', () => dir.value, () => null)
    await nextTick()
    dir.value = '/b'
    await flush()
    first.reject(new Error('late'))
    await flush()
    expect(info.value).toEqual({ branch: 'b' })
  })
})
