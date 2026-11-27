import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { answerBox, useBoxAnswer } from './useBoxAnswer'
import { en } from '@/locales/en'

const boxes = [{ name: 'local' }, { name: 'my box' }, { name: 'a%20b' }]

describe('answerBox', () => {
  it('knows a box in the list, also with a space or a percent sign in the name', () => {
    expect(answerBox('local', boxes)).toEqual({ kind: 'known', box: boxes[0] })
    expect(answerBox('my box', boxes)).toEqual({ kind: 'known', box: boxes[1] })
    expect(answerBox('a%20b', boxes)).toEqual({ kind: 'known', box: boxes[2] })
  })

  it('answers none for a missing, empty or null box', () => {
    for (const raw of [undefined, null, '', []]) expect(answerBox(raw, boxes)).toEqual({ kind: 'none' })
  })

  it('answers unknown with the name for a box nobody has', () => {
    expect(answerBox('ghost', boxes)).toEqual({ kind: 'unknown', name: 'ghost' })
    expect(answerBox('Local', boxes)).toEqual({ kind: 'unknown', name: 'Local' })
  })

  it('takes the first value of a repeated query', () => {
    expect(answerBox(['local', 'x'], boxes)).toEqual({ kind: 'known', box: boxes[0] })
  })
})

describe('useBoxAnswer', () => {
  it('turns unknown when the box is removed while the page is open, with the one text', () => {
    const list = ref([...boxes])
    const { answer, unknownText } = useBoxAnswer(() => 'my box', () => list.value)
    expect(answer.value.kind).toBe('known')
    expect(unknownText.value).toBeNull()
    list.value = list.value.filter((box) => box.name !== 'my box')
    expect(answer.value).toEqual({ kind: 'unknown', name: 'my box' })
    expect(unknownText.value).toBe(en['terminal.unknown_box'])
  })
})
