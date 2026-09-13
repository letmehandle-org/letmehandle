/**
 * Design tokens.
 *
 * Every colour, space, radius, shadow and type value in the application comes from here, and
 * these are the values in `design/tokens.css` — the design is the source, this is its
 * translation. A literal in a component is a value that cannot be changed in one place.
 *
 * The design is drawn on a 268-point frame standing in for a ~390-point phone, so its sizes are
 * scaled by roughly 1.4 here: a 12-pixel row title in the design is the 17-point body text iOS
 * itself uses.
 *
 * Light ground, a pastel hero, one violet. Violet always means the assistant, apricot only ever
 * means it needs the user, and red is kept apart for destroying things.
 */
import type { TextStyle, ViewStyle } from 'react-native';

const colour = {
  /** The app's ground, tinted toward the primary. */
  background: '#F8F6FF',
  /** Cards, sheets, the tab bar. */
  surface: '#FFFFFF',
  /** Inset: fields, pressed rows, segmented controls. */
  well: '#F1EEFC',
  border: '#E9E4F7',
  borderSoft: '#F1EDFB',

  text: '#1D1436',
  textMuted: '#7A72A0',
  textFaint: '#9A93BC',
  textGhost: '#C6BFDF',
  /** Text on a filled primary control. */
  onAccent: '#FFFFFF',

  /** The assistant. The bright shade fills; the deep shade carries text. */
  accent: '#7C6BEA',
  accentDeep: '#4F3FB8',
  accentWash: '#E6E1FF',
  accentSpark: '#B9AAFF',

  /** Needs the user — and nothing else. */
  needsYou: '#FBA36B',
  needsYouDeep: '#B5641D',
  needsYouWash: '#FFEBD8',

  /** A refused call is the product working, so it is neutral rather than red. */
  quietWash: '#EFECF8',

  /** Destructive, and never the brand colour. */
  warning: '#C2364E',
  warningWash: '#FFE3E8',

  heroTop: '#E9E3FF',
  heroBottom: '#D8CFFB',
  heroText: '#2C1F63',
  /** The hero when a call needs the user: the same card, in apricot. */
  heroNeedsTop: '#FFEBD8',
  heroNeedsBottom: '#FFD9B8',
  heroMuted: '#7166AC',
  /** The unfilled part of the ring. */
  dialRest: '#E4DEF6',
  dialRestOnHero: 'rgba(255,255,255,0.74)',
} as const;

const space = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 40,
  /** Between the rows of a card. */
  row: 12,
  /** The screen's side gutter. Never varies. */
  gutter: 20,
} as const;

const radius = {
  sm: 12,
  md: 20,
  lg: 28,
  pill: 999,
} as const;

/**
 * Poppins, in the three weights the design uses and no others.
 *
 * The weight is in the family name rather than in `fontWeight`: a bundled font is a file per
 * weight, and asking Android for weight 600 of "Poppins" falls back to the system font.
 */
const font = {
  light: 'Poppins-Light',
  regular: 'Poppins-Regular',
  strong: 'Poppins-SemiBold',
} as const;

const type = {
  /** The number in the middle of the ring. */
  figure: { fontFamily: font.light, fontSize: 52, lineHeight: 58 },
  title: { fontFamily: font.strong, fontSize: 28, lineHeight: 35 },
  heading: { fontFamily: font.strong, fontSize: 21, lineHeight: 28 },
  body: { fontFamily: font.regular, fontSize: 17, lineHeight: 24 },
  strong: { fontFamily: font.strong, fontSize: 17, lineHeight: 24 },
  subtitle: { fontFamily: font.regular, fontSize: 15, lineHeight: 21 },
  caption: { fontFamily: font.regular, fontSize: 13, lineHeight: 18 },
  label: {
    fontFamily: font.strong,
    fontSize: 11,
    lineHeight: 14,
    letterSpacing: 1.2,
    textTransform: 'uppercase',
  },
  button: { fontFamily: font.strong, fontSize: 17, lineHeight: 22 },
} as const satisfies Record<string, TextStyle>;

/** Shadows as both platforms spell them: iOS draws the offset, Android the elevation. */
const shadow = {
  card: {
    shadowColor: '#2E1F63',
    shadowOpacity: 0.07,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  raised: {
    shadowColor: '#2E1F63',
    shadowOpacity: 0.13,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
  accent: {
    shadowColor: '#7C6BEA',
    shadowOpacity: 0.3,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 6 },
    elevation: 4,
  },
} as const satisfies Record<string, ViewStyle>;

/** The one size every tappable thing is at least. */
const touch = { min: 48 } as const;

export const theme = {
  colour,
  space,
  radius,
  font,
  type,
  shadow,
  touch,
} as const;

export type Theme = typeof theme;
