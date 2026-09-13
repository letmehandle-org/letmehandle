/**
 * Setting up, from the application's point of view.
 *
 * The whole tree against a backend that remembers, because the properties worth proving are all
 * about what happens between requests: where somebody is put when they open the application,
 * which of the server's steps they are never shown, and what they are told when a save is
 * refused.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { twoLanes } from '../preferences/rules';
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

async function next(view: View, from: string, to: string): Promise<void> {
  await waitFor(() => {
    expect(view.getByTestId(from)).toBeOnTheScreen();
  });
  await fireEvent.press(view.getByTestId('onboarding-continue'));
  await waitFor(() => {
    expect(view.getByTestId(to)).toBeOnTheScreen();
  });
}

describe('where somebody lands', () => {
  it('passes the introduction by and opens on how calls work', async () => {
    // The welcome page before sign-in is the introduction; asking it again is a wasted step.
    const backend = runningBackend({ startAt: 'introduction' });
    const view = await render(<App />);

    expect(
      await view.findByTestId('onboarding-call_handling'),
    ).toBeOnTheScreen();
    expect(backend.onboarding().completed).toContain('introduction');
    expect(view.getByTestId('lane-contacts')).toBeOnTheScreen();
    expect(view.getByTestId('lane-unknown')).toBeOnTheScreen();
  });

  it('resumes at the step the server says is next, not at the beginning', async () => {
    // Somebody who reinstalled, or signed in on a second phone.
    runningBackend({ startAt: 'authority' });
    const view = await render(<App />);

    expect(await view.findByTestId('onboarding-authority')).toBeOnTheScreen();
    expect(view.getByTestId('steps')).toHaveAccessibleName(
      en.setup.progress.replace('{{done}}', '3').replace('{{total}}', '4'),
    );
  });

  it('opens the application itself once there is nothing left to ask', async () => {
    runningBackend({ startAt: null });
    const view = await render(<App />);

    expect(await view.findByTestId('home-screen')).toBeOnTheScreen();
    // Setup was finished elsewhere, so the "all set" page is not for this person.
    expect(view.queryByTestId('setup-done')).toBeNull();
  });
});

describe('the four steps', () => {
  it('saves each answer, never asks the steps the design leaves out, and ends on "all set"', async () => {
    const backend = runningBackend({ startAt: 'call_handling' });
    const view = await render(<App />);

    await next(view, 'onboarding-call_handling', 'onboarding-hours');
    expect(backend.preferences().call_handling).toEqual(
      twoLanes(DEFAULT_PREFERENCES.call_handling),
    );
    // Important contacts sits between the two on the server, and was passed over.
    expect(backend.onboarding().completed).toContain('important_contacts');
    expect(view.queryByTestId('onboarding-important_contacts')).toBeNull();

    expect(view.getByTestId('hours-always')).toBeOnTheScreen();
    await next(view, 'onboarding-hours', 'onboarding-authority');
    expect(backend.preferences().hours).toEqual({ working: null, quiet: null });

    await fireEvent(
      view.getByTestId('capability-take_a_message'),
      'valueChange',
      true,
    );
    await next(view, 'onboarding-authority', 'onboarding-notifications');
    expect(backend.preferences().authority.capabilities).toEqual([
      'take_a_message',
    ]);

    expect(view.getByTestId('call-graph')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('onboarding-continue'));

    // Personality is the last step on the server and is passed over too.
    expect(await view.findByTestId('setup-done')).toBeOnTheScreen();
    expect(backend.onboarding().is_complete).toBe(true);
    expect(await view.findByTestId('setup-done-voice')).toHaveTextContent(
      en.setup.done.voice.replace('{{voice}}', 'Ash'),
    );

    await fireEvent.press(view.getByTestId('setup-done-home'));
    expect(await view.findByTestId('home-screen')).toBeOnTheScreen();
  });

  it('stays on the step and says so when a save is refused', async () => {
    const backend = runningBackend({ startAt: 'call_handling' });
    const view = await render(<App />);
    await view.findByTestId('onboarding-call_handling');

    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });
    await fireEvent.press(view.getByTestId('onboarding-continue'));

    expect(await view.findByTestId('onboarding-problem')).toHaveTextContent(
      en.setup.saveFailed,
    );
    expect(view.getByTestId('onboarding-call_handling')).toBeOnTheScreen();
    expect(backend.onboarding().next_step).toBe('call_handling');
  });

  it('offers no skip on any step the design shows', async () => {
    runningBackend({ startAt: 'call_handling' });
    const view = await render(<App />);
    await view.findByTestId('onboarding-call_handling');

    expect(view.queryByText(en.setup.skip)).toBeNull();
  });
});

describe('a step that cannot be passed over', () => {
  it('offers another try when recording it fails', async () => {
    const backend: RunningBackend = runningBackend({ startAt: 'introduction' });
    const original = globalThis.fetch;
    let failed = false;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      if (
        !failed &&
        url.endsWith('/v1/onboarding') &&
        init?.method === 'POST'
      ) {
        failed = true;
        return {
          ok: false,
          status: 503,
          json: async () => ({ error: 'unavailable', message: 'x' }),
        } as Response;
      }
      return original(url, init);
    }) as typeof fetch;

    const view = await render(<App />);
    expect(await view.findByTestId('onboarding-retry')).toBeOnTheScreen();

    await fireEvent.press(view.getByTestId('onboarding-retry'));
    expect(
      await view.findByTestId('onboarding-call_handling'),
    ).toBeOnTheScreen();
    expect(backend.onboarding().completed).toContain('introduction');
  });
});
