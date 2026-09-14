/** The timer functions Jest can fake. */
const FAKEABLE = [
  'Date',
  'hrtime',
  'nextTick',
  'performance',
  'queueMicrotask',
  'requestAnimationFrame',
  'cancelAnimationFrame',
  'requestIdleCallback',
  'cancelIdleCallback',
  'setImmediate',
  'clearImmediate',
  'setInterval',
  'clearInterval',
  'setTimeout',
  'clearTimeout',
] as const;

export type Fakeable = (typeof FAKEABLE)[number];

/** Fakes only the named timer functions; everything else keeps running on real time. */
export function fakeOnly(...faked: readonly Fakeable[]): void {
  jest.useFakeTimers({
    doNotFake: FAKEABLE.filter(name => !faked.includes(name)),
  });
}
