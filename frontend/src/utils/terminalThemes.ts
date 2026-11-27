import type { ITheme } from '@xterm/xterm'

/**
 * The terminal themes a box can be given: one table, keyed by name, and the picker lists
 * them in this key order. `calm` is drawn from the app's palette and is what a box with no
 * saved theme gets; a saved theme is kept as it is.
 */
// The calm theme is the app's palette (main.css): the page's surface behind the text, its
// secondary text colour, the accent for the cursor, and the status colours for the ANSI
// ones. Magenta and cyan have no token; theirs are working values. A light page gets the
// light palette's twin, CALM_LIGHT.
export const TERMINAL_THEMES = {
  calm: {
    background: '#171a20',      // --surface
    foreground: '#cbd5e1',      // --text-secondary
    cursor: '#5b9dff',          // --accent
    cursorAccent: '#171a20',
    selectionBackground: '#5b9dff40',
    black: '#2a313d',           // --stroke
    red: '#f87171',             // --error
    green: '#22c55e',           // --success
    yellow: '#f59e0b',          // --warning
    blue: '#5b9dff',            // --accent
    magenta: '#c4a7f5',
    cyan: '#67c7d4',
    white: '#cbd5e1',           // --text-secondary
    brightBlack: '#8b95a5',     // --muted
    brightRed: '#fca5a5',       // --error-hover
    brightGreen: '#4ade80',     // --success-hover
    brightYellow: '#fbbf24',    // --warning-hover
    brightBlue: '#76aeff',      // --accent-hover
    brightMagenta: '#d8c4fa',
    brightCyan: '#8ad8e2',
    brightWhite: '#f1f5f9',     // --text
  },
  cyberpunk: {
    background: '#0a0e14',      // Deep near-black
    foreground: '#b3b1ad',      // Muted warm gray
    cursor: '#e6b450',          // Amber cursor
    cursorAccent: '#0a0e14',
    selectionBackground: '#273747',
    selectionForeground: '#e6e6e6',
    // ANSI colors tuned for Starship + readability
    black: '#01060e',
    red: '#ea6c73',             // Errors: visible but not screaming
    green: '#91b362',           // Success: calm green
    yellow: '#f9af4f',          // Warnings: readable amber
    blue: '#53bdfa',            // Info: cool blue
    magenta: '#fae994',         // Accent
    cyan: '#90e1c6',            // Secondary
    white: '#c7c7c7',
    brightBlack: '#686868',
    brightRed: '#f07178',
    brightGreen: '#c2d94c',
    brightYellow: '#ffb454',
    brightBlue: '#59c2ff',
    brightMagenta: '#ffee99',
    brightCyan: '#95e6cb',
    brightWhite: '#ffffff',
  },
  default: {
    background: '#0f1115',
    foreground: '#e6e6e6',
    cursor: '#6aa6ff',
    cursorAccent: '#0f1115',
    selectionBackground: '#4a86df80',
    black: '#1a1a1a',
    red: '#ff6a6a',
    green: '#3ba86a',
    yellow: '#f5c542',
    blue: '#6aa6ff',
    magenta: '#c678dd',
    cyan: '#56b6c2',
    white: '#e6e6e6',
    brightBlack: '#6b7280',
    brightRed: '#ff8787',
    brightGreen: '#5fd75f',
    brightYellow: '#ffd75f',
    brightBlue: '#85b8ff',
    brightMagenta: '#d19aff',
    brightCyan: '#76d7c4',
    brightWhite: '#ffffff',
  },
  solarized: {
    background: '#002b36',
    foreground: '#839496',
    cursor: '#839496',
    cursorAccent: '#002b36',
    selectionBackground: '#073642',
    black: '#073642',
    red: '#dc322f',
    green: '#859900',
    yellow: '#b58900',
    blue: '#268bd2',
    magenta: '#d33682',
    cyan: '#2aa198',
    white: '#eee8d5',
    brightBlack: '#002b36',
    brightRed: '#cb4b16',
    brightGreen: '#586e75',
    brightYellow: '#657b83',
    brightBlue: '#839496',
    brightMagenta: '#6c71c4',
    brightCyan: '#93a1a1',
    brightWhite: '#fdf6e3',
  },
  dracula: {
    background: '#282a36',
    foreground: '#f8f8f2',
    cursor: '#f8f8f2',
    cursorAccent: '#282a36',
    selectionBackground: '#44475a',
    black: '#000000',
    red: '#ff5555',
    green: '#50fa7b',
    yellow: '#f1fa8c',
    blue: '#bd93f9',
    magenta: '#ff79c6',
    cyan: '#8be9fd',
    white: '#bfbfbf',
    brightBlack: '#4d4d4d',
    brightRed: '#ff6e67',
    brightGreen: '#5af78e',
    brightYellow: '#f4f99d',
    brightBlue: '#caa9fa',
    brightMagenta: '#ff92d0',
    brightCyan: '#9aedfe',
    brightWhite: '#e6e6e6',
  },
  nord: {
    background: '#2e3440',
    foreground: '#d8dee9',
    cursor: '#d8dee9',
    cursorAccent: '#2e3440',
    selectionBackground: '#4c566a',
    black: '#3b4252',
    red: '#bf616a',
    green: '#a3be8c',
    yellow: '#ebcb8b',
    blue: '#81a1c1',
    magenta: '#b48ead',
    cyan: '#88c0d0',
    white: '#e5e9f0',
    brightBlack: '#4c566a',
    brightRed: '#bf616a',
    brightGreen: '#a3be8c',
    brightYellow: '#ebcb8b',
    brightBlue: '#81a1c1',
    brightMagenta: '#b48ead',
    brightCyan: '#8fbcbb',
    brightWhite: '#eceff4',
  },
  monokai: {
    background: '#272822',
    foreground: '#f8f8f2',
    cursor: '#f8f8f0',
    cursorAccent: '#272822',
    selectionBackground: '#49483e',
    black: '#272822',
    red: '#f92672',
    green: '#a6e22e',
    yellow: '#f4bf75',
    blue: '#66d9ef',
    magenta: '#ae81ff',
    cyan: '#a1efe4',
    white: '#f8f8f2',
    brightBlack: '#75715e',
    brightRed: '#f92672',
    brightGreen: '#a6e22e',
    brightYellow: '#f4bf75',
    brightBlue: '#66d9ef',
    brightMagenta: '#ae81ff',
    brightCyan: '#a1efe4',
    brightWhite: '#f9f8f5',
  },
  light: {
    background: '#fafafa',
    foreground: '#383a42',
    cursor: '#526eff',
    cursorAccent: '#fafafa',
    selectionBackground: '#bfceff',
    selectionForeground: '#383a42',
    black: '#383a42',
    red: '#e45649',
    green: '#50a14f',
    yellow: '#c18401',
    blue: '#4078f2',
    magenta: '#a626a4',
    cyan: '#0184bc',
    white: '#a0a1a7',
    brightBlack: '#4f525e',
    brightRed: '#e06c75',
    brightGreen: '#98c379',
    brightYellow: '#e5c07b',
    brightBlue: '#61afef',
    brightMagenta: '#c678dd',
    brightCyan: '#56b6c2',
    brightWhite: '#c0c1c7',
  }
} satisfies Record<string, ITheme>

const CALM_LIGHT: ITheme = {
  background: '#ffffff',        // --surface
  foreground: '#374151',        // --text-secondary
  cursor: '#2563eb',            // --accent
  cursorAccent: '#ffffff',
  selectionBackground: '#2563eb33',
  black: '#111827',             // --text
  red: '#dc2626',               // --error
  green: '#16a34a',             // --success
  yellow: '#d97706',            // --warning
  blue: '#2563eb',              // --accent
  magenta: '#9333ea',
  cyan: '#0e7490',
  white: '#8b95a5',
  brightBlack: '#64748b',       // --muted
  brightRed: '#b91c1c',         // --error-hover
  brightGreen: '#15803d',       // --success-hover
  brightYellow: '#b45309',      // --warning-hover
  brightBlue: '#1d4ed8',        // --accent-hover
  brightMagenta: '#7e22ce',
  brightCyan: '#155e75',
  brightWhite: '#bcc4d2',       // --stroke-hover
}

export type TerminalThemeName = keyof typeof TERMINAL_THEMES

export const TERMINAL_THEME_NAMES = Object.keys(TERMINAL_THEMES) as TerminalThemeName[]

export const DEFAULT_TERMINAL_THEME: TerminalThemeName = 'calm'

/** The theme a box's saved value means: the value if it names a theme, else the default. */
export function terminalThemeOf(saved: string | null | undefined): TerminalThemeName {
  return (TERMINAL_THEME_NAMES as readonly string[]).includes(saved ?? '')
    ? (saved as TerminalThemeName)
    : DEFAULT_TERMINAL_THEME
}

// The colours a theme name means on a light or a dark page: the calm theme follows the
// page, and so does cyberpunk (a light page shows One Light for it, as before); every
// other theme is the same on both.
export const terminalColours = (name: TerminalThemeName, dark: boolean): ITheme => {
  if (name === 'calm') return dark ? TERMINAL_THEMES.calm : CALM_LIGHT
  if (name === 'cyberpunk' && !dark) return TERMINAL_THEMES.light
  return TERMINAL_THEMES[name]
}
