/**
 * What is kept, for how long, and deleting the account.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { RETENTION_DAYS } from '../screens/settings/PrivacyScreen';
import {
  DEFAULT_PREFERENCES,
  runningBackend,
  type RunningBackend,
} from './support/backend';

jest.mock('../auth/tokenStore', () => ({
  ...jest.requireActual('../auth/tokenStore'),
  loadSession: jest.fn(async () => ({
    accessToken: 'a-token',
    refreshToken: 'a-refresh-token',
    accessTokenExpiresAt: Date.now() + 600_000,
  })),
  saveSession: jest.fn(async () => undefined),
  clearSession: jest.fn(async () => undefined),
}));

type View = Awaited<ReturnType<typeof render>>;

async function openPrivacy(
  days = 7,
): Promise<{ backend: RunningBackend; view: View }> {
  const backend = runningBackend({
    startAt: null,
    preferences: {
      ...DEFAULT_PREFERENCES,
      privacy: { transcript_retention_days: days },
    },
  });
  const view = await render(<App />);
  await view.findByTestId('home-screen');
  await fireEvent.press(view.getByTestId('tab-settings'));
  expect(await view.findByTestId('settings-open-privacy')).toHaveAccessibleName(
    `${en.settings.privacy}, ${days === 1 ? '1 day' : `${days} days`}`,
  );
  await fireEvent.press(view.getByTestId('settings-open-privacy'));
  await view.findByTestId('settings-privacy');
  return { backend, view };
}

describe('how long words are kept', () => {
  it('offers a day to ninety days inside what the API accepts, and no "never"', () => {
    expect(RETENTION_DAYS).toEqual([1, 7, 30, 90]);
  });

  it('marks the stored length and saves a new one as it is tapped', async () => {
    const { backend, view } = await openPrivacy();
    expect(view.getByTestId('privacy-retention-7')).toBeChecked();

    await fireEvent.press(view.getByTestId('privacy-retention-30'));
    await waitFor(() => {
      expect(backend.preferences().privacy.transcript_retention_days).toBe(30);
    });
    expect(backend.patches).toEqual([
      { privacy: { transcript_retention_days: 30 } },
    ]);
    await waitFor(() => {
      expect(view.getByTestId('privacy-retention-30')).toBeChecked();
    });
  });

  it('still shows a length set elsewhere that is not one of the four', async () => {
    const { view } = await openPrivacy(14);
    expect(view.getByTestId('privacy-retention-14')).toBeChecked();
  });

  it('puts a refused change back and says why', async () => {
    const { backend, view } = await openPrivacy();
    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });

    await fireEvent.press(view.getByTestId('privacy-retention-1'));

    expect(await view.findByTestId('settings-problem')).toHaveTextContent(
      en.common.saveFailed,
    );
    expect(view.getByTestId('privacy-retention-7')).toBeChecked();
  });

  it('states that calls are never recorded, rather than offering a switch', async () => {
    const { view } = await openPrivacy();
    expect(view.getByTestId('privacy-recordings')).toHaveTextContent(
      en.privacy.recordings,
    );
    expect(view.queryByRole('switch')).toBeNull();
  });
});

describe('deleting the account', () => {
  it('says exactly what goes, then deletes and signs out', async () => {
    const { backend, view } = await openPrivacy();

    await fireEvent.press(view.getByTestId('privacy-delete-account'));
    expect(view.getByTestId('delete-account-sheet')).toHaveTextContent(
      /cannot be undone/,
    );
    expect(view.getByTestId('delete-account-sheet')).toHaveTextContent(
      /A call in progress is ended first/,
    );
    await fireEvent.press(view.getByTestId('delete-account-sheet-confirm'));

    expect(await view.findByTestId('welcome-screen')).toBeOnTheScreen();
    expect(backend.accountDeleted()).toBe(true);
  });

  it('keeps the account when asked to', async () => {
    const { backend, view } = await openPrivacy();
    await fireEvent.press(view.getByTestId('privacy-delete-account'));
    await fireEvent.press(view.getByTestId('delete-account-sheet-keep'));

    expect(backend.accountDeleted()).toBe(false);
    expect(view.getByTestId('settings-privacy')).toBeOnTheScreen();
  });

  it('stays signed in and says so when the deletion fails', async () => {
    const { backend, view } = await openPrivacy();
    backend.failNext('/v1/me', {
      status: 500,
      body: { error: 'internal_error', message: 'x' },
    });

    await fireEvent.press(view.getByTestId('privacy-delete-account'));
    await fireEvent.press(view.getByTestId('delete-account-sheet-confirm'));

    expect(await view.findByText(en.deleteAccount.failed)).toBeOnTheScreen();
    expect(view.getByTestId('settings-privacy')).toBeOnTheScreen();
  });
});
