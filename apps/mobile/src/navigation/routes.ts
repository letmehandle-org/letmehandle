/**
 * The route map.
 *
 * Typed, and the single source of both names and parameters. A string route name at a call
 * site is a typo waiting to become a crash on a screen nobody tested.
 *
 * Two stacks, not one. Which screens exist depends on whether somebody is signed in, and
 * expressing that as two maps means a signed-out person cannot navigate to the application by
 * any route — rather than being guarded by a check on every screen.
 */
export type AuthStackParamList = {
  Welcome: undefined;
  PhoneNumber: undefined;
  VerifyCode: { challengeId: string; phoneNumber: string };
};

export type AppStackParamList = {
  Home: undefined;
  Profile: undefined;
};

export const AUTH_ROUTES = {
  welcome: 'Welcome',
  phoneNumber: 'PhoneNumber',
  verifyCode: 'VerifyCode',
} as const satisfies Record<string, keyof AuthStackParamList>;

export const APP_ROUTES = {
  home: 'Home',
  profile: 'Profile',
} as const satisfies Record<string, keyof AppStackParamList>;
