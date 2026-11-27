import { computed, type ComputedRef } from 'vue'

import { useI18n } from '@/i18n'

export type BoxAnswer<B extends { name: string }> =
  | { kind: 'known'; box: B }
  | { kind: 'none' }
  | { kind: 'unknown'; name: string }

type RouteBox = string | null | undefined | Array<string | null>

/** The one answer to "which box does this page mean": known, none, or unknown (a name nobody has). */
export function answerBox<B extends { name: string }>(raw: RouteBox, boxes: readonly B[]): BoxAnswer<B> {
  const name = (Array.isArray(raw) ? raw[0] : raw) ?? ''
  if (!name) return { kind: 'none' }
  const box = boxes.find((candidate) => candidate.name === name)
  return box ? { kind: 'known', box } : { kind: 'unknown', name }
}

/**
 * Reactive form of `answerBox`. `unknownText` is the one text for the unknown case, so a page
 * shows the same words whichever view it is.
 */
export function useBoxAnswer<B extends { name: string }>(
  routeBox: () => RouteBox,
  boxes: () => readonly B[],
): { answer: ComputedRef<BoxAnswer<B>>; unknownText: ComputedRef<string | null> } {
  const { t } = useI18n()
  const answer = computed(() => answerBox(routeBox(), boxes()))
  const unknownText = computed(() => {
    const current = answer.value
    return current.kind === 'unknown' ? t('terminal.unknown_box') : null
  })
  return { answer, unknownText }
}
