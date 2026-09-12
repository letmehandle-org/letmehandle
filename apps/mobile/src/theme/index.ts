/**
 * Design tokens.
 *
 * Every colour, space and type value in the application comes from here. A literal in a
 * component is a value that cannot be changed in one place, and phase 9 replaces these with the
 * real design — which is only cheap if nothing has hard-coded them in the meantime.
 *
 * Dark first, black as the primary surface.
 */
export const theme = {
  colour: {
    background: '#000000',
    surface: '#0E0E10',
    border: '#1F1F23',
    text: '#FFFFFF',
    textMuted: '#9A9AA2',
    accent: '#5B8CFF',
  },
  space: {
    xs: 4,
    sm: 8,
    md: 16,
    lg: 24,
    xl: 40,
  },
  radius: {
    sm: 8,
    md: 14,
  },
  type: {
    title: { fontSize: 32, fontWeight: '700' },
    body: { fontSize: 16, fontWeight: '400' },
  },
} as const;

export type Theme = typeof theme;
