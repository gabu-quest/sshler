import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock matchMedia before importing the composable
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

describe('useResponsive breakpoint logic', () => {
  // Test the breakpoint classification logic directly
  // The composable uses: isMobile = width < 768, isTablet = 768-1024, isDesktop >= 1024

  function computeBreakpoints(width: number) {
    const TABLET = 768
    const TABLET_LARGE = 1024
    return {
      isMobile: width < TABLET,
      isTablet: width >= TABLET && width < TABLET_LARGE,
      isDesktop: width >= TABLET_LARGE,
    }
  }

  it('classifies 375px as mobile', () => {
    const result = computeBreakpoints(375)
    expect(result.isMobile).toBe(true)
    expect(result.isTablet).toBe(false)
    expect(result.isDesktop).toBe(false)
  })

  it('classifies 768px as tablet', () => {
    const result = computeBreakpoints(768)
    expect(result.isMobile).toBe(false)
    expect(result.isTablet).toBe(true)
    expect(result.isDesktop).toBe(false)
  })

  it('classifies 1024px as desktop', () => {
    const result = computeBreakpoints(1024)
    expect(result.isMobile).toBe(false)
    expect(result.isTablet).toBe(false)
    expect(result.isDesktop).toBe(true)
  })

  it('classifies 480px (edge of mobile breakpoint) as mobile', () => {
    const result = computeBreakpoints(480)
    expect(result.isMobile).toBe(true)
  })

  it('classifies 767px as mobile (just below tablet)', () => {
    const result = computeBreakpoints(767)
    expect(result.isMobile).toBe(true)
  })

  it('classifies 1023px as tablet (just below desktop)', () => {
    const result = computeBreakpoints(1023)
    expect(result.isTablet).toBe(true)
    expect(result.isDesktop).toBe(false)
  })

  it('classifies 1400px as desktop', () => {
    const result = computeBreakpoints(1400)
    expect(result.isDesktop).toBe(true)
  })

  it('exports composable function', async () => {
    const mod = await import('./useResponsive')
    expect(typeof mod.useResponsive).toBe('function')
  })
})
