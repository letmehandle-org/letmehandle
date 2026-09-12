/**
 * The route map.
 *
 * Typed, and the single source of both names and parameters. A string route name at a call
 * site is a typo waiting to become a crash on a screen nobody tested.
 *
 * Three stacks, not one. Which screens exist depends on whether somebody is signed in and
 * whether they have finished setting up, and expressing that as three maps means a signed-out
 * person cannot navigate to the application by any route, and somebody halfway through setup
 * cannot navigate past it — rather than being guarded by a check on every screen.
 */
import type { PreferenceSection } from '../preferences/options';

export type AuthStackParamList = {
  Welcome: undefined;
  PhoneNumber: undefined;
  VerifyCode: { challengeId: string; phoneNumber: string };
};

export type OnboardingStackParamList = {
  Step: undefined;
};

export type AppStackParamList = {
  Home: undefined;
  Profile: undefined;
  Settings: undefined;
  SettingsSection: { section: PreferenceSection };
  Voice: undefined;
  CallScreening: undefined;
};

export const AUTH_ROUTES = {
  welcome: 'Welcome',
  phoneNumber: 'PhoneNumber',
  verifyCode: 'VerifyCode',
} as const satisfies Record<string, keyof AuthStackParamList>;

export const ONBOARDING_ROUTES = {
  step: 'Step',
} as const satisfies Record<string, keyof OnboardingStackParamList>;

export const APP_ROUTES = {
  home: 'Home',
  profile: 'Profile',
  settings: 'Settings',
  settingsSection: 'SettingsSection',
  voice: 'Voice',
  callScreening: 'CallScreening',
} as const satisfies Record<string, keyof AppStackParamList>;
