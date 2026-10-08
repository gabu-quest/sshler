import { describe, expect, it } from 'vitest'
import { en } from './en'
import { ja } from './ja'

// Mutation killed: deleting any key from either locale (or adding one to only
// one). The assertion lists the exact offending keys, so the failure names them.
describe('locale key parity', () => {
  const enKeys = Object.keys(en)
  const jaKeys = Object.keys(ja)

  it('every English key exists in Japanese', () => {
    expect(enKeys.filter((k) => !(k in ja))).toEqual([])
  })

  it('every Japanese key exists in English', () => {
    expect(jaKeys.filter((k) => !(k in en))).toEqual([])
  })

  it('no value is empty in either locale', () => {
    const empty = (locale: Record<string, string>) =>
      Object.entries(locale).filter(([, v]) => v.trim() === '').map(([k]) => k)
    expect(empty(en)).toEqual([])
    expect(empty(ja)).toEqual([])
  })

  it('placeholders match between locales for every key', () => {
    const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort()
    const mismatched = Object.entries(en)
      .filter(([k, v]) => {
        const jaValue = ja[k]
        return jaValue !== undefined && ph(v).join() !== ph(jaValue).join()
      })
      .map(([k]) => k)
    expect(mismatched).toEqual([])
  })
})
