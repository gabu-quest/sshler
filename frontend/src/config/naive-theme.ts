import type { GlobalThemeOverrides } from 'naive-ui'

export const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    fontWeight: '450',
    fontWeightStrong: '600',
    // Ensure text is readable in light mode
    textColor1: '#1a1a1a',
    textColor2: '#333333',
    textColor3: '#666666',
    // Card/surface backgrounds — warm grey, not blinding white
    cardColor: '#f0f1f4',
    bodyColor: '#eaecf0',
    modalColor: '#f4f5f7',
    popoverColor: '#f4f5f7',
    tableColor: '#f0f1f4',
    inputColor: '#f4f5f7',
    // Borders — slightly more visible
    borderColor: '#d8dbe0',
    dividerColor: '#d8dbe0',
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
    colorEmbedded: '#e8eaee',
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
