import { ref, readonly, onMounted, onUnmounted } from 'vue'

const BREAKPOINTS = {
  mobile: 480,
  tablet: 768,
  tabletLarge: 1024,
  desktop: 1400,
} as const

export function useResponsive() {
  const width = ref(typeof window !== 'undefined' ? window.innerWidth : 1024)
  const isMobile = ref(false)
  const isTablet = ref(false)
  const isDesktop = ref(false)
  const isTouch = ref(false)

  const update = () => {
    width.value = window.innerWidth
    isMobile.value = width.value < BREAKPOINTS.tablet
    isTablet.value = width.value >= BREAKPOINTS.tablet && width.value < BREAKPOINTS.tabletLarge
    isDesktop.value = width.value >= BREAKPOINTS.tabletLarge
  }

  const checkTouch = () => {
    isTouch.value = window.matchMedia('(hover: none) and (pointer: coarse)').matches
  }

  let resizeHandler: (() => void) | null = null

  onMounted(() => {
    update()
    checkTouch()

    let timeout: number | null = null
    resizeHandler = () => {
      if (timeout) clearTimeout(timeout)
      timeout = window.setTimeout(update, 100)
    }

    window.addEventListener('resize', resizeHandler)

    // Listen for touch capability changes (e.g. tablet docking)
    const touchQuery = window.matchMedia('(hover: none) and (pointer: coarse)')
    touchQuery.addEventListener('change', checkTouch)
  })

  onUnmounted(() => {
    if (resizeHandler) {
      window.removeEventListener('resize', resizeHandler)
    }
  })

  return {
    width: readonly(width),
    isMobile: readonly(isMobile),
    isTablet: readonly(isTablet),
    isDesktop: readonly(isDesktop),
    isTouch: readonly(isTouch),
    breakpoints: BREAKPOINTS,
  }
}
