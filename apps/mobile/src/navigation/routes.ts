/**
 * The route map.
 *
 * Typed, and the single source of both names and parameters. A string route name at a call site
 * is a typo waiting to become a runtime crash on a screen nobody tested.
 */
export type RootStackParamList = {
  Home: undefined;
};

export const ROUTES = {
  home: 'Home',
} as const satisfies Record<string, keyof RootStackParamList>;
