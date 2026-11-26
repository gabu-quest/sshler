import type { GlobalThemeOverrides } from 'naive-ui'

// The same colours as the tokens in styles/main.css (`[data-theme="light"]` and
// `[data-theme="dark"]`); the comment after each value names its token. App.spec.ts
// holds the two in step.

const shared: GlobalThemeOverrides = {
  Button: {
    fontWeight: '550',
  },
  Tag: {
    fontWeight: '550',
  },
  DataTable: {
    fontWeight: '450',
  },
}

export const lightThemeOverrides: GlobalThemeOverrides = {
  ...shared,
  common: {
    fontWeight: '450',
    fontWeightStrong: '600',
    borderRadius: '6px', // --radius-md
    bodyColor: '#f4f5f7', // --bg
    cardColor: '#ffffff', // --surface
    tableColor: '#ffffff', // --surface
    tableColorStriped: '#f8f9fb', // --table-stripe
    modalColor: '#ffffff', // --surface-raised
    popoverColor: '#ffffff', // --surface-raised
    inputColor: '#ffffff', // --input-bg
    borderColor: '#d8dde6', // --stroke
    dividerColor: '#d8dde6', // --stroke
    textColor1: '#111827', // --text
    textColor2: '#374151', // --text-secondary
    textColor3: '#64748b', // --muted
    primaryColor: '#2563eb', // --accent
    primaryColorHover: '#1d4ed8', // --accent-hover
    primaryColorPressed: '#1e40af', // --accent-pressed
    primaryColorSuppl: '#2563eb', // --accent
    successColor: '#16a34a', // --success
    successColorHover: '#15803d', // --success-hover
    successColorPressed: '#15803d', // --success-hover
    successColorSuppl: '#16a34a', // --success
    warningColor: '#d97706', // --warning
    warningColorHover: '#b45309', // --warning-hover
    warningColorPressed: '#b45309', // --warning-hover
    warningColorSuppl: '#d97706', // --warning
    errorColor: '#dc2626', // --error
    errorColorHover: '#b91c1c', // --error-hover
    errorColorPressed: '#b91c1c', // --error-hover
    errorColorSuppl: '#dc2626', // --error
  },
  Card: {
    colorEmbedded: '#eef0f4', // --surface-variant
  },
}

export const darkThemeOverrides: GlobalThemeOverrides = {
  ...shared,
  common: {
    fontWeight: '450',
    fontWeightStrong: '600',
    borderRadius: '6px', // --radius-md
    bodyColor: '#111317', // --bg
    cardColor: '#171a20', // --surface
    tableColor: '#171a20', // --surface
    tableColorStriped: '#1b1f27', // --table-stripe
    modalColor: '#1a1e26', // --surface-raised
    popoverColor: '#1a1e26', // --surface-raised
    inputColor: '#14171d', // --input-bg
    borderColor: '#2a313d', // --stroke
    dividerColor: '#2a313d', // --stroke
    textColor1: '#f1f5f9', // --text
    textColor2: '#cbd5e1', // --text-secondary
    textColor3: '#8b95a5', // --muted
    primaryColor: '#5b9dff', // --accent
    primaryColorHover: '#76aeff', // --accent-hover
    primaryColorPressed: '#4084e8', // --accent-pressed
    primaryColorSuppl: '#5b9dff', // --accent
    successColor: '#22c55e', // --success
    successColorHover: '#4ade80', // --success-hover
    successColorPressed: '#4ade80', // --success-hover
    successColorSuppl: '#22c55e', // --success
    warningColor: '#f59e0b', // --warning
    warningColorHover: '#fbbf24', // --warning-hover
    warningColorPressed: '#fbbf24', // --warning-hover
    warningColorSuppl: '#f59e0b', // --warning
    errorColor: '#f87171', // --error
    errorColorHover: '#fca5a5', // --error-hover
    errorColorPressed: '#fca5a5', // --error-hover
    errorColorSuppl: '#f87171', // --error
  },
  Card: {
    colorEmbedded: '#1f242d', // --surface-variant
  },
}
