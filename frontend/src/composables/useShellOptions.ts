import { computed } from 'vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { useI18n } from '@/i18n'

/** The two shells a POSIX local box and a remote box offer. */
/** localStorage key of the Windows shell the user last picked; read by both terminal views. */
export const WINDOWS_SHELL_KEY = 'sshler.windows_shell'

export type PosixShell = 'tmux' | 'plain'

/** Any stored or received value that is not exactly `plain` is tmux, the default. */
export function normalizePosixShell(value: unknown): PosixShell {
  return value === 'plain' ? 'plain' : 'tmux'
}

/**
 * The owner of the Shell option list: both terminal views read it here.
 *
 * A Windows local box offers its native shells (the server's list); every other box
 * offers "tmux" and "Plain shell".
 */
export function useShellOptions() {
  const bootstrapStore = useBootstrapStore()
  const boxesStore = useBoxesStore()
  const { t } = useI18n()

  const isWindowsLocalBox = (boxName: string | null | undefined): boolean =>
    bootstrapStore.isWindows &&
    boxesStore.items.find((box) => box.name === boxName)?.transport === 'local'

  const optionsFor = (boxName: string | null | undefined) =>
    isWindowsLocalBox(boxName)
      ? bootstrapStore.windowsShells.map((shell) => ({
          label: shell.available ? shell.label : `${shell.label} (not installed)`,
          value: shell.id,
        }))
      : [
          { label: t('terminal.shell_tmux'), value: 'tmux' },
          { label: t('terminal.shell_plain'), value: 'plain' },
        ]

  /** The options as a computed for one box that may change. */
  const optionsOf = (boxName: () => string | null | undefined) => computed(() => optionsFor(boxName()))

  return { isWindowsLocalBox, optionsFor, optionsOf }
}
