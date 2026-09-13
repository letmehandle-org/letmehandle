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
    // Asserts the route names; `satisfies` already checks them against the parameter lists.
    expect(Object.values(AUTH_ROUTES)).toEqual([
      'Welcome',
      'PhoneNumber',
      'VerifyCode',
    ]);
    expect(Object.values(APP_ROUTES)).toEqual([
      'Tabs',
      'Who',
      'When',
      'Hours',
      'Authority',
      'Say',
      'Personalise',
      'Topics',
      'Account',
      'Privacy',
      'CallScreening',
      'Call',
      'Transcript',
      'Escalation',
    ]);
    expect(Object.values(ONBOARDING_ROUTES)).toEqual(['Step']);
  });

  it('keeps the two stacks separate', () => {
    // The stacks' route maps share no screens.
    const shared = Object.values(AUTH_ROUTES).filter(name =>
      (Object.values(APP_ROUTES) as string[]).includes(name),
    );
    expect(shared).toEqual([]);
  });
});
