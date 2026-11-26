import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { normalizePosixShell, useShellOptions } from './useShellOptions'

const TMUX_AND_PLAIN = [
  { label: 'tmux', value: 'tmux' },
  { label: 'Plain shell', value: 'plain' },
]

function setup(platform: string) {
  setActivePinia(createPinia())
  const bootstrap = useBootstrapStore()
  bootstrap.payload = {
    platform,
    windows_shells: [
      { id: 'pwsh', label: 'PowerShell 7', available: true },
      { id: 'wsl', label: 'WSL', available: false },
    ],
  } as never
  useBoxesStore().items = [
    { name: 'local', host: 'localhost', transport: 'local' },
    { name: 'build', host: 'build.example', transport: 'ssh' },
  ] as never
  return useShellOptions()
}

describe('useShellOptions', () => {
  beforeEach(() => localStorage.clear())

  it('a POSIX local box offers exactly tmux and Plain shell', () => {
    expect(setup('posix').optionsFor('local')).toEqual(TMUX_AND_PLAIN)
  })

  it('a remote box offers exactly tmux and Plain shell, also when the page runs on Windows', () => {
    expect(setup('posix').optionsFor('build')).toEqual(TMUX_AND_PLAIN)
    expect(setup('windows').optionsFor('build')).toEqual(TMUX_AND_PLAIN)
  })

  it('a Windows local box keeps the server list, with its not-installed mark', () => {
    const shells = setup('windows')
    expect(shells.isWindowsLocalBox('local')).toBe(true)
    expect(shells.optionsFor('local')).toEqual([
      { label: 'PowerShell 7', value: 'pwsh' },
      { label: 'WSL (not installed)', value: 'wsl' },
    ])
  })

  it('a box the page does not know offers tmux and Plain shell', () => {
    expect(setup('posix').optionsFor('nowhere')).toEqual(TMUX_AND_PLAIN)
  })

  it('only the word plain is plain: anything else is tmux', () => {
    expect(normalizePosixShell('plain')).toBe('plain')
    for (const v of ['tmux', '', 'PLAIN', 'zsh', 7, null, undefined, {}]) {
      expect(normalizePosixShell(v)).toBe('tmux')
    }
  })
})
