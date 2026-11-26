/**
 * The terminal themes a box can be given, in the order the theme picker lists them. Their
 * colours live in `Terminal.vue`. `calm` is drawn from the app's palette and is what a box
 * with no saved theme gets; a saved theme is kept as it is.
 */
export const TERMINAL_THEME_NAMES = [
  'calm', 'cyberpunk', 'default', 'solarized', 'dracula', 'nord', 'monokai', 'light',
] as const

export type TerminalThemeName = typeof TERMINAL_THEME_NAMES[number]

export const DEFAULT_TERMINAL_THEME: TerminalThemeName = 'calm'

/** The theme a box's saved value means: the value if it names a theme, else the default. */
export function terminalThemeOf(saved: string | null | undefined): TerminalThemeName {
  return (TERMINAL_THEME_NAMES as readonly string[]).includes(saved ?? '')
    ? (saved as TerminalThemeName)
    : DEFAULT_TERMINAL_THEME
}
