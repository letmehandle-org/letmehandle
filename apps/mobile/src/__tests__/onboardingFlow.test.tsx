/**
 * Setting up, from the application's point of view.
 *
 * The whole tree against a backend that remembers, because the properties worth proving are all
 * about what happens between requests: where somebody is put when they open the application,
 * what they are allowed to skip, and what they are told when a save is refused.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { runningBackend, type RunningBackend } from './support/backend';

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

async function open(backend: RunningBackend) {
  const view = await render(<App />);
  const step = backend.onboarding().next_step;
  await waitFor(() => {
    expect(view.getByTestId(`onboarding-${step}`)).toBeOnTheScreen();
  });
  return view;
}

describe('where somebody lands', () => {
  it('opens setup when there is still something to ask', async () => {
    const backend = runningBackend({ startAt: 'introduction' });
    const view = await open(backend);

    expect(view.getByTestId('onboarding-introduction')).toBeOnTheScreen();
  });

  it('resumes at the step the server says is next, not at the beginning', async () => {
    // Somebody who reinstalled, or signed in on a second phone. Asking them everything again is
    // the reason the progress is held on the server at all.
    const backend = runningBackend({ startAt: 'authority' });
    const view = await open(backend);

    expect(view.getByTestId('onboarding-authority')).toBeOnTheScreen();
    expect(view.queryByTestId('onboarding-introduction')).toBeNull();
  });

  it('opens the application itself once there is nothing left to ask', async () => {
    runningBackend({ startAt: null });
    const view = await render(<App />);

    expect(await view.findByTestId('home-screen')).toBeOnTheScreen();
  });
});

describe('answering', () => {
  it('records the introduction without saving anything', async () => {
    const backend = runningBackend({ startAt: 'introduction' });
    const view = await open(backend);

    await fireEvent.press(view.getByTestId('onboarding-continue'));

    expect(
      await view.findByTestId('onboarding-call_handling'),
    ).toBeOnTheScreen();
    expect(backend.patches).toHaveLength(0);
  });

  it('saves the answer and moves on', async () => {
    const backend = runningBackend({ startAt: 'call_handling' });
    const view = await open(backend);

    await fireEvent.press(view.getByTestId('handling-default-reject'));
    await fireEvent.press(view.getByTestId('onboarding-continue'));

    await waitFor(() => {
      expect(backend.preferences().call_handling.default_posture).toBe(
        'reject',
      );
    });
    expect(
      await view.findByTestId('onboarding-important_contacts'),
    ).toBeOnTheScreen();
  });

  it('walks the whole way through to the application', async () => {
    const backend = runningBackend({ startAt: 'introduction' });
    const view = await open(backend);

    for (const step of [
      'introduction',
      'call_handling',
      'important_contacts',
      'hours',
      'authority',
      'notifications',
      'personality',
    ]) {
      await waitFor(() => {
        expect(view.getByTestId(`onboarding-${step}`)).toBeOnTheScreen();
      });
      await fireEvent.press(view.getByTestId('onboarding-continue'));
    }

    expect(await view.findByTestId('home-screen')).toBeOnTheScreen();
  });
});

describe('skipping', () => {
  it('is not offered for call handling, which has no safe default', async () => {
    // The backend answers 422 for this step, so offering the button would be showing somebody a
    // failure they could not have avoided.
    const backend = runningBackend({ startAt: 'call_handling' });
    const view = await open(backend);

    expect(view.queryByTestId('onboarding-skip')).toBeNull();
  });

  it('is not offered for the introduction, which asks nothing', async () => {
    const backend = runningBackend({ startAt: 'introduction' });
    const view = await open(backend);

    expect(view.queryByTestId('onboarding-skip')).toBeNull();
  });

  it('passes over a step the server allows, without saving it', async () => {
    const backend = runningBackend({ startAt: 'hours' });
    const view = await open(backend);

    await fireEvent.press(view.getByTestId('onboarding-skip'));

    expect(await view.findByTestId('onboarding-authority')).toBeOnTheScreen();
    expect(backend.patches).toHaveLength(0);
  });
});

describe('when a save is refused', () => {
  it('stays on the step and says so', async () => {
    const backend = runningBackend({ startAt: 'call_handling' });
    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });
    const view = await open(backend);

    await fireEvent.press(view.getByTestId('handling-default-reject'));
    await fireEvent.press(view.getByTestId('onboarding-continue'));

    expect(await view.findByText(en.onboarding.saveFailed)).toBeOnTheScreen();
    expect(view.getByTestId('onboarding-call_handling')).toBeOnTheScreen();
  });

  it('says plainly when the service cannot be reached', async () => {
    const backend = runningBackend({ startAt: 'notifications' });
    const view = await open(backend);

    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    await fireEvent.press(view.getByTestId('onboarding-continue'));

    expect(await view.findByText(en.common.noConnection)).toBeOnTheScreen();
  });
});
