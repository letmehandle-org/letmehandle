import { render } from '@testing-library/react-native';
import React from 'react';

import { SessionProvider } from '../auth/SessionProvider';
import { initialiseI18n } from '../i18n';
import { RootNavigator } from '../navigation/RootNavigator';
import {
  APP_ROUTES,
  AUTH_ROUTES,
  ONBOARDING_ROUTES,
} from '../navigation/routes';

jest.mock('../auth/tokenStore', () => ({
  ...jest.requireActual('../auth/tokenStore'),
  loadSession: jest.fn(async () => null),
  saveSession: jest.fn(async () => undefined),
  clearSession: jest.fn(async () => undefined),
}));

describe('RootNavigator', () => {
  beforeAll(async () => {
    await initialiseI18n('en');
  });

  it('shows the sign-in screens when nobody is signed in', async () => {
    const view = await render(
      <SessionProvider>
        <RootNavigator />
      </SessionProvider>,
    );

    expect(await view.findByTestId('welcome-screen')).toBeOnTheScreen();
  });

  it('names every route in the typed maps', () => {
    // The maps and the parameter lists are kept in step by `satisfies`, so this asserts the
    // names themselves rather than the types, which the compiler has already checked.
    expect(Object.values(AUTH_ROUTES)).toEqual([
      'Welcome',
      'PhoneNumber',
      'VerifyCode',
    ]);
    expect(Object.values(APP_ROUTES)).toEqual([
      'Home',
      'Profile',
      'Settings',
      'SettingsSection',
      'Voice',
    ]);
    expect(Object.values(ONBOARDING_ROUTES)).toEqual(['Step']);
  });

  it('keeps the two stacks separate', () => {
    // A signed-out person has no route into the application, and a signed-in one has no route
    // back to the sign-in screens. That is a property of the maps, not of a guard on a screen.
    const shared = Object.values(AUTH_ROUTES).filter(name =>
      (Object.values(APP_ROUTES) as string[]).includes(name),
    );
    expect(shared).toEqual([]);
  });
});
