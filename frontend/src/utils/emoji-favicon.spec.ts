import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createEmojiFavicon,
  fnv1aHash,
  getEmojiForBox,
  getEmojiForString,
  resetFavicon,
  setEmojiFavicon,
} from './emoji-favicon'

describe('fnv1aHash', () => {
  // Mutation killed: changing the offset basis, the prime, or dropping `>>> 0`.
  it('matches published FNV-1a 32-bit vectors', () => {
    expect(fnv1aHash('')).toBe(2166136261)
    expect(fnv1aHash('a')).toBe(3826002220)
    expect(fnv1aHash('local')).toBe(2621662984)
  })
})

describe('emoji pools', () => {
  // Mutation killed: moving any emoji so it appears in both pools, or making a
  // pick non-deterministic (e.g. adding Math.random).
  it('box and directory pools are disjoint and have exact sizes', () => {
    const boxes = new Set<string>()
    const dirs = new Set<string>()
    for (let i = 0; i < 20000; i++) {
      boxes.add(getEmojiForBox(`box${i}`))
      dirs.add(getEmojiForString(`box:/p/${i}`))
    }
    expect(boxes.size).toBe(30)
    expect(dirs.size).toBe(146)
    expect([...boxes].filter((e) => dirs.has(e))).toEqual([])
  })

  // Mutation killed: changing the modulo target, the hash, or the pool order.
  it('box picks are golden', () => {
    expect(getEmojiForBox('local')).toBe('🛸')
    expect(getEmojiForBox('web')).toBe('🎡')
    expect(getEmojiForBox('prod')).toBe('🚃')
    expect(getEmojiForBox('sshler')).toBe('⛩️')
  })

  it('directory picks are golden', () => {
    expect(getEmojiForString('local:/home/u')).toBe('🍪')
    expect(getEmojiForString('web:/srv/app')).toBe('🍋')
    expect(getEmojiForString('x')).toBe('🎺')
  })

  it('is deterministic for repeated calls', () => {
    expect(getEmojiForString('web:/srv/app')).toBe(getEmojiForString('web:/srv/app'))
    expect(getEmojiForBox('web')).toBe(getEmojiForBox('web'))
  })

  // Mutation killed: removing the empty-string fallbacks.
  it('empty input falls back to fixed emojis', () => {
    expect(getEmojiForString('')).toBe('📁')
    expect(getEmojiForBox('')).toBe('🖥️')
  })
})

describe('favicon DOM helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    document.head.innerHTML = ''
  })

  function stubCanvas() {
    const fillText = vi.fn()
    const ctx = { clearRect: vi.fn(), fillText, font: '', textAlign: '', textBaseline: '' }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never)
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,STUB')
    return { fillText }
  }

  // Mutation killed: drawing a different emoji, or discarding the data URL.
  it('createEmojiFavicon draws the emoji and returns the data URL', () => {
    const { fillText } = stubCanvas()
    expect(createEmojiFavicon('🦊')).toBe('data:image/png;base64,STUB')
    expect(fillText).toHaveBeenCalledWith('🦊', 16, 18)
  })

  // Mutation killed: not returning '' when there is no 2d context.
  it('createEmojiFavicon returns "" without a 2d context', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    expect(createEmojiFavicon('🦊')).toBe('')
  })

  // Mutation killed: creating a second <link> instead of reusing, or using the wrong emoji.
  it('setEmojiFavicon creates one icon link, then reuses it', () => {
    const { fillText } = stubCanvas()
    setEmojiFavicon('x')
    setEmojiFavicon('x')
    const links = document.head.querySelectorAll('link[rel="icon"]')
    expect(links).toHaveLength(1)
    expect((links[0] as HTMLLinkElement).href).toBe('data:image/png;base64,STUB')
    expect(fillText).toHaveBeenLastCalledWith('🎺', 16, 18)
  })

  // Mutation killed: writing a link even when canvas is unavailable.
  it('setEmojiFavicon adds no link when the canvas fails', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    setEmojiFavicon('x')
    expect(document.head.querySelectorAll('link[rel="icon"]')).toHaveLength(0)
  })

  // Mutation killed: pointing reset at a different default path.
  it('resetFavicon restores the default href and is a no-op without a link', () => {
    resetFavicon()
    expect(document.head.querySelectorAll('link')).toHaveLength(0)
    stubCanvas()
    setEmojiFavicon('x')
    resetFavicon()
    expect(document.head.querySelector('link[rel="icon"]')?.getAttribute('href')).toBe(
      '/app/favicon.png',
    )
  })
})
