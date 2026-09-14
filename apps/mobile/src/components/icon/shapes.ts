/** The icon set from `design/icons.svg`: 24-point squares of 1.5-point strokes, named by meaning. */
export type IconShape =
  | { readonly kind: 'path'; readonly d: string }
  | {
      readonly kind: 'circle';
      readonly cx: number;
      readonly cy: number;
      readonly r: number;
    }
  | {
      readonly kind: 'rect';
      readonly x: number;
      readonly y: number;
      readonly width: number;
      readonly height: number;
      readonly rx: number;
    };

export const ICONS = {
  home: [
    { kind: 'path', d: 'M3.5 10.2 12 3.6l8.5 6.6' },
    { kind: 'path', d: 'M5.8 9.2V20h12.4V9.2' },
    { kind: 'path', d: 'M9.8 20v-5.2h4.4V20' },
  ],
  activity: [{ kind: 'path', d: 'M3 12.5h3.6L9 5.5l4 13 2.4-6h5.6' }],
  settings: [
    { kind: 'path', d: 'M4 7.5h16M4 16.5h16' },
    { kind: 'circle', cx: 9.5, cy: 7.5, r: 2.4 },
    { kind: 'circle', cx: 15, cy: 16.5, r: 2.4 },
  ],
  phone: [
    {
      kind: 'path',
      d: 'M8.2 4.5H5.4A1.6 1.6 0 0 0 3.8 6.2c0 7.7 6.3 14 14 14a1.6 1.6 0 0 0 1.7-1.6v-2.8l-4-1.6-1.9 2.2a13.4 13.4 0 0 1-6-6l2.2-1.9z',
    },
  ],
  'phone-in': [
    {
      kind: 'path',
      d: 'M7.6 5.2H5.2a1.5 1.5 0 0 0-1.5 1.6c0 7.3 5.9 13.2 13.2 13.2a1.5 1.5 0 0 0 1.6-1.5V16l-3.8-1.5-1.8 2.1a12.6 12.6 0 0 1-5.6-5.6l2.1-1.8z',
    },
    { kind: 'path', d: 'M20.5 3.5 15 9' },
    { kind: 'path', d: 'M15 4.6V9h4.4' },
  ],
  'phone-off': [
    {
      kind: 'path',
      d: 'M7.6 5.2H5.2a1.5 1.5 0 0 0-1.5 1.6c0 7.3 5.9 13.2 13.2 13.2a1.5 1.5 0 0 0 1.6-1.5V16l-3.8-1.5-1.8 2.1a12.6 12.6 0 0 1-5.6-5.6l2.1-1.8z',
    },
    { kind: 'path', d: 'M15 4.5l5.5 5.5M20.5 4.5 15 10' },
  ],
  shield: [
    {
      kind: 'path',
      d: 'M12 3.2 19 6v5.8c0 4.4-2.9 7.4-7 8.9-4.1-1.5-7-4.5-7-8.9V6z',
    },
    { kind: 'path', d: 'M9 12l2.2 2.2L15.4 10' },
  ],
  bell: [
    {
      kind: 'path',
      d: 'M6.2 10a5.8 5.8 0 0 1 11.6 0c0 4.6 1.9 5.8 1.9 5.8H4.3s1.9-1.2 1.9-5.8z',
    },
    { kind: 'path', d: 'M10 19a2 2 0 0 0 4 0' },
  ],
  'bell-off': [
    {
      kind: 'path',
      d: 'M6.2 10a5.8 5.8 0 0 1 8.4-5.2M17.8 12.5c.3 2.6 1.9 3.3 1.9 3.3H7',
    },
    { kind: 'path', d: 'M10 19a2 2 0 0 0 4 0' },
    { kind: 'path', d: 'M4 3.5 20 20' },
  ],
  clock: [
    { kind: 'circle', cx: 12, cy: 12, r: 8.4 },
    { kind: 'path', d: 'M12 7.4V12l3.2 2' },
  ],
  moon: [
    { kind: 'path', d: 'M20 14.3A8.6 8.6 0 0 1 9.7 4 8.6 8.6 0 1 0 20 14.3z' },
  ],
  sun: [
    { kind: 'circle', cx: 12, cy: 12, r: 4.2 },
    {
      kind: 'path',
      d: 'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4',
    },
  ],
  user: [
    { kind: 'circle', cx: 12, cy: 8.4, r: 3.6 },
    { kind: 'path', d: 'M4.8 20c0-3.6 3.2-5.8 7.2-5.8s7.2 2.2 7.2 5.8' },
  ],
  users: [
    { kind: 'circle', cx: 9.5, cy: 8.6, r: 3.2 },
    { kind: 'path', d: 'M3.6 19.5c0-3.2 2.6-5.2 5.9-5.2s5.9 2 5.9 5.2' },
    {
      kind: 'path',
      d: 'M16.4 6.2a3.1 3.1 0 0 1 0 5.6M17.6 14.6c2 .6 3.4 2.2 3.4 4.6',
    },
  ],
  truck: [
    { kind: 'path', d: 'M2.8 6.6h10.4v9.2H2.8z' },
    { kind: 'path', d: 'M13.2 9.6h3.6l2.8 2.9v3.3h-6.4z' },
    { kind: 'circle', cx: 7, cy: 17.6, r: 1.8 },
    { kind: 'circle', cx: 16.6, cy: 17.6, r: 1.8 },
  ],
  health: [
    {
      kind: 'path',
      d: 'M12 19.8C8.4 17.6 4 14.4 4 10.4a3.9 3.9 0 0 1 8-1.5 3.9 3.9 0 0 1 8 1.5c0 4-4.4 7.2-8 9.4z',
    },
  ],
  card: [
    { kind: 'rect', x: 3, y: 5.6, width: 18, height: 12.8, rx: 2.2 },
    { kind: 'path', d: 'M3 10h18' },
    { kind: 'path', d: 'M6.6 14.4h3.4' },
  ],
  school: [
    { kind: 'path', d: 'M12 4.4 21 8.6 12 12.8 3 8.6z' },
    { kind: 'path', d: 'M7 10.8v4.4c0 1.4 2.4 2.6 5 2.6s5-1.2 5-2.6v-4.4' },
  ],
  tag: [
    { kind: 'path', d: 'M11.4 3.4H20v8.6l-8.6 8.6L3 12z' },
    { kind: 'circle', cx: 16.2, cy: 7.8, r: 1.3 },
  ],
  ban: [
    { kind: 'circle', cx: 12, cy: 12, r: 8.4 },
    { kind: 'path', d: 'M6 6l12 12' },
  ],
  help: [
    { kind: 'circle', cx: 12, cy: 12, r: 8.4 },
    { kind: 'path', d: 'M9.6 9.4a2.5 2.5 0 1 1 3.3 2.4c-.6.3-.9.8-.9 1.5v.4' },
    { kind: 'path', d: 'M12 16.8h.01' },
  ],
  check: [{ kind: 'path', d: 'M4.8 12.6 9.6 17.4 19.4 6.9' }],
  'check-c': [
    { kind: 'circle', cx: 12, cy: 12, r: 8.4 },
    { kind: 'path', d: 'M8.4 12.2 11 14.8l4.8-5.2' },
  ],
  x: [{ kind: 'path', d: 'M6.4 6.4 17.6 17.6M17.6 6.4 6.4 17.6' }],
  'chev-r': [{ kind: 'path', d: 'M9.5 5.5 16 12l-6.5 6.5' }],
  'chev-l': [{ kind: 'path', d: 'M14.5 5.5 8 12l6.5 6.5' }],
  'chev-d': [{ kind: 'path', d: 'M5.5 9.5 12 16l6.5-6.5' }],
  plus: [{ kind: 'path', d: 'M12 5.2v13.6M5.2 12h13.6' }],
  trash: [
    { kind: 'path', d: 'M4.6 6.8h14.8' },
    { kind: 'path', d: 'M9.4 6.8V4.6h5.2v2.2' },
    { kind: 'path', d: 'M6.6 6.8 7.6 20h8.8l1-13.2' },
  ],
  lock: [
    { kind: 'rect', x: 5, y: 10.4, width: 14, height: 9.4, rx: 2.2 },
    { kind: 'path', d: 'M8.2 10.4V7.8a3.8 3.8 0 0 1 7.6 0v2.6' },
  ],
  key: [
    { kind: 'circle', cx: 8, cy: 12, r: 3.8 },
    { kind: 'path', d: 'M11.8 12H20' },
    { kind: 'path', d: 'M17 12v3.2M14.2 12v2.4' },
  ],
  mic: [
    { kind: 'rect', x: 9.2, y: 3.4, width: 5.6, height: 10.4, rx: 2.8 },
    { kind: 'path', d: 'M5.6 11.6a6.4 6.4 0 0 0 12.8 0' },
    { kind: 'path', d: 'M12 18v2.6' },
  ],
  speaker: [
    { kind: 'path', d: 'M4.6 9.4h3.2L12 5.6v12.8l-4.2-3.8H4.6z' },
    {
      kind: 'path',
      d: 'M15.4 9.6a3.4 3.4 0 0 1 0 4.8M17.8 7.2a6.8 6.8 0 0 1 0 9.6',
    },
  ],
  play: [{ kind: 'path', d: 'M8.4 5.6 18 12l-9.6 6.4z' }],
  pause: [{ kind: 'path', d: 'M9.4 5.6v12.8M14.6 5.6v12.8' }],
  globe: [
    { kind: 'circle', cx: 12, cy: 12, r: 8.4 },
    { kind: 'path', d: 'M3.6 12h16.8' },
    {
      kind: 'path',
      d: 'M12 3.6c2.2 2.4 3.3 5.3 3.3 8.4S14.2 18 12 20.4C9.8 18 8.7 15.1 8.7 12S9.8 6 12 3.6z',
    },
  ],
  alert: [
    { kind: 'path', d: 'M12 4.4 21 19.6H3z' },
    { kind: 'path', d: 'M12 10v4' },
    { kind: 'path', d: 'M12 17.2h.01' },
  ],
  info: [
    { kind: 'circle', cx: 12, cy: 12, r: 8.4 },
    { kind: 'path', d: 'M12 11v5.2' },
    { kind: 'path', d: 'M12 7.8h.01' },
  ],
  doc: [
    { kind: 'path', d: 'M6 3.6h7.4L18 8.2V20.4H6z' },
    { kind: 'path', d: 'M13.2 3.6v4.8H18' },
    { kind: 'path', d: 'M9 13h6M9 16.4h4' },
  ],
  msg: [{ kind: 'path', d: 'M4.4 5.6h15.2v10.2H10l-4.2 3.4v-3.4H4.4z' }],
  'wifi-off': [
    {
      kind: 'path',
      d: 'M3.4 8.6a14 14 0 0 1 5-3M20.6 8.6a14 14 0 0 0-8.6-3.4',
    },
    {
      kind: 'path',
      d: 'M6.8 12.2a9 9 0 0 1 3-1.9M17.2 12.2a9 9 0 0 0-2.4-1.6',
    },
    { kind: 'path', d: 'M9.8 15.6a4.4 4.4 0 0 1 4.4 0' },
    { kind: 'path', d: 'M12 18.8h.01' },
    { kind: 'path', d: 'M3.6 3.6 20.4 20.4' },
  ],
  refresh: [
    { kind: 'path', d: 'M20 12a8 8 0 1 1-2.6-5.9' },
    { kind: 'path', d: 'M20 4.4V9h-4.6' },
  ],
  filter: [{ kind: 'path', d: 'M3.6 5.6h16.8l-6.4 7.6v6l-4 1.8v-7.8z' }],
  search: [
    { kind: 'circle', cx: 10.8, cy: 10.8, r: 6.4 },
    { kind: 'path', d: 'M15.6 15.6 20.4 20.4' },
  ],
  cal: [
    { kind: 'rect', x: 3.6, y: 5.4, width: 16.8, height: 15, rx: 2.2 },
    { kind: 'path', d: 'M3.6 10h16.8M8.4 3.4v4M15.6 3.4v4' },
  ],
  pin: [
    {
      kind: 'path',
      d: 'M12 21c4-4.6 6-7.7 6-10.2A6 6 0 0 0 6 10.8C6 13.3 8 16.4 12 21z',
    },
    { kind: 'circle', cx: 12, cy: 10.6, r: 2.3 },
  ],
  'eye-off': [
    {
      kind: 'path',
      d: 'M4 12s3.4-5.4 8-5.4 8 5.4 8 5.4-3.4 5.4-8 5.4S4 12 4 12z',
    },
    { kind: 'circle', cx: 12, cy: 12, r: 2.4 },
    { kind: 'path', d: 'M4 4l16 16' },
  ],
  'hand-off': [
    { kind: 'circle', cx: 12, cy: 12, r: 8.4 },
    { kind: 'path', d: 'M8.4 12h7.2' },
    { kind: 'path', d: 'M12.8 8.8 16 12l-3.2 3.2' },
  ],
  power: [
    { kind: 'path', d: 'M12 4v8' },
    { kind: 'path', d: 'M17.7 7a8 8 0 1 1-11.4 0' },
  ],
  'arrow-r': [
    { kind: 'path', d: 'M4.6 12h14.8' },
    { kind: 'path', d: 'M14.4 7 19.4 12l-5 5' },
  ],
  sliders: [
    {
      kind: 'path',
      d: 'M6 4.6v5M6 14v5.4M12 4.6v9M12 17.4v2M18 4.6v2.4M18 11.4v8',
    },
    { kind: 'circle', cx: 6, cy: 11.6, r: 2 },
    { kind: 'circle', cx: 12, cy: 15.4, r: 2 },
    { kind: 'circle', cx: 18, cy: 9, r: 2 },
  ],
  'dot-grid': [
    { kind: 'circle', cx: 12, cy: 5.5, r: 1.4 },
    { kind: 'circle', cx: 12, cy: 12, r: 1.4 },
    { kind: 'circle', cx: 12, cy: 18.5, r: 1.4 },
  ],
  bot: [
    { kind: 'rect', x: 5, y: 8.2, width: 14, height: 10.8, rx: 3.2 },
    { kind: 'path', d: 'M12 8.2V5.4' },
    { kind: 'circle', cx: 12, cy: 4.2, r: 1.1 },
    { kind: 'circle', cx: 9.4, cy: 13, r: 1 },
    { kind: 'circle', cx: 14.6, cy: 13, r: 1 },
    { kind: 'path', d: 'M10.2 16.1h3.6' },
    { kind: 'path', d: 'M3 12.3v3.2M21 12.3v3.2' },
  ],
  'bubble-q': [
    {
      kind: 'path',
      d: 'M5 4.8h14a2 2 0 0 1 2 2v8.6a2 2 0 0 1-2 2h-8.3L6.5 20.6v-3.2H5a2 2 0 0 1-2-2V6.8a2 2 0 0 1 2-2z',
    },
    { kind: 'path', d: 'M10.2 9.2a1.9 1.9 0 1 1 2.9 1.6c-.6.4-1.1.8-1.1 1.6' },
    { kind: 'path', d: 'M12 14.6h.01' },
  ],
  siren: [
    { kind: 'path', d: 'M7.2 17.2v-4.4a4.8 4.8 0 0 1 9.6 0v4.4' },
    { kind: 'rect', x: 5, y: 17.2, width: 14, height: 3, rx: 1 },
    { kind: 'path', d: 'M12 3v1.8M4.4 6.2l1.3 1.3M19.6 6.2l-1.3 1.3' },
    { kind: 'path', d: 'M12 10.6v3.2' },
  ],
  'phone-ring': [
    {
      kind: 'path',
      d: 'M7.4 5.8H5.1a1.5 1.5 0 0 0-1.5 1.6c0 7.2 5.8 13 13 13a1.5 1.5 0 0 0 1.6-1.5v-2.3l-3.7-1.5-1.8 2.1a12.4 12.4 0 0 1-5.5-5.5l2.1-1.8z',
    },
    { kind: 'path', d: 'M14.6 3.6a6 6 0 0 1 5.8 5.8' },
    { kind: 'path', d: 'M14.4 7.1a2.6 2.6 0 0 1 2.5 2.5' },
  ],
  inbox: [
    {
      kind: 'path',
      d: 'M3.6 13.6 6 6.4a1.6 1.6 0 0 1 1.5-1.1h9a1.6 1.6 0 0 1 1.5 1.1l2.4 7.2v4.8a1.4 1.4 0 0 1-1.4 1.4H5a1.4 1.4 0 0 1-1.4-1.4z',
    },
    { kind: 'path', d: 'M3.6 13.6h4.8l1.3 2.5h4.6l1.3-2.5h4.8' },
  ],
  sparkle: [
    {
      kind: 'path',
      d: 'M12 3.5c.6 4.6 1.9 5.9 6.5 6.5-4.6.6-5.9 1.9-6.5 6.5-.6-4.6-1.9-5.9-6.5-6.5 4.6-.6 5.9-1.9 6.5-6.5z',
    },
    {
      kind: 'path',
      d: 'M18.5 15.5c.2 1.6.7 2.1 2.3 2.3-1.6.2-2.1.7-2.3 2.3-.2-1.6-.7-2.1-2.3-2.3 1.6-.2 2.1-.7 2.3-2.3z',
    },
  ],
  infinity: [
    {
      kind: 'path',
      d: 'M12 12c-2-2.6-3.6-4-5.6-4a4 4 0 0 0 0 8c2 0 3.6-1.4 5.6-4zm0 0c2 2.6 3.6 4 5.6 4a4 4 0 0 0 0-8c-2 0-3.6 1.4-5.6 4z',
    },
  ],
} as const satisfies Record<string, readonly IconShape[]>;

export type IconName = keyof typeof ICONS;
