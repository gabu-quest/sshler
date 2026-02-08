import type { GlobalThemeOverrides } from 'naive-ui'

export const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    fontWeight: '450',
    fontWeightStrong: '600',
    // Ensure text is readable in light mode
    textColor1: '#1a1a1a',
    textColor2: '#333333',
    textColor3: '#666666',
    // Card/surface backgrounds
    cardColor: '#ffffff',
    bodyColor: '#f8f9fa',
    modalColor: '#ffffff',
    popoverColor: '#ffffff',
    tableColor: '#ffffff',
    inputColor: '#ffffff',
    // Borders
    borderColor: '#e5e7eb',
    dividerColor: '#e5e7eb',
    // Primary color
    primaryColor: '#6aa6ff',
    primaryColorHover: '#5a96ef',
    primaryColorPressed: '#4a86df',
    primaryColorSuppl: '#6aa6ff',
  },
  Button: {
    fontWeight: '550',
  },
  Tag: {
    fontWeight: '550',
  },
  Card: {
    colorEmbedded: '#f1f3f5',
  },
  DataTable: {
    fontWeight: '450',
  },
}

export const darkThemeOverrides: GlobalThemeOverrides = {
  common: {
    fontWeight: '450',
    fontWeightStrong: '600',
  },
  Button: {
    fontWeight: '550',
  },
  Tag: {
    fontWeight: '550',
  },
}
