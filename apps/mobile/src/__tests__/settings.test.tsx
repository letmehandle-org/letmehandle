/**
 * Changing things after setup is over.
 *
 * Nothing in this product is set once, so the test that matters most is the dull one: every
 * page reachable, every change saved the moment it is made, and a refused save put back and
 * explained.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import type { Preferences } from '@letmehandle/api-client';

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

const LANES: Preferences = {
  ...DEFAULT_PREFERENCES,
  call_handling: twoLanes(DEFAULT_PREFERENCES.call_handling),
};

async function openSettings(): Promise<View> {
  const view = await render(<App />);
  await waitFor(() => {
    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
  });
  await fireEvent.press(view.getByTestId('tab-settings'));
  await waitFor(() => {
    expect(view.getByTestId('settings-screen')).toBeOnTheScreen();
  });
  return view;
}

async function openPage(page: string): Promise<View> {
  const view = await openSettings();
  await fireEvent.press(view.getByTestId(`settings-open-${page}`));
  await waitFor(() => {
    expect(view.getByTestId(`settings-${page}`)).toBeOnTheScreen();
  });
  return view;
}

async function savedOnce(
  backend: RunningBackend,
  before: number,
): Promise<void> {
  await waitFor(() => {
    expect(backend.patches.length).toBe(before + 1);
  });
}

describe('the settings list', () => {
  it('shows where each setting stands without opening it', async () => {
    runningBackend({ startAt: null, preferences: LANES });
    const view = await openSettings();

    expect(view.getByTestId('settings-open-who')).toHaveAccessibleName(
      `${en.settings.who}, ${en.settings.whoValue}`,
    );
    expect(view.getByTestId('settings-open-hours')).toHaveAccessibleName(
      `${en.settings.hours}, ${en.hours.always}`,
    );
    expect(view.getByTestId('settings-open-authority')).toHaveAccessibleName(
      `${en.settings.authority}, ${en.settings.authorityValue
        .replace('{{granted}}', '0')
        .replace('{{total}}', '7')}`,
    );
    expect(view.getByTestId('settings-open-say')).toHaveAccessibleName(
      `${en.settings.say}, ${en.settings.sayNothing}`,
    );
  });

  it('names calls set up another way as custom', async () => {
    runningBackend({ startAt: null });
    const view = await openSettings();
    expect(view.getByTestId('settings-open-who')).toHaveAccessibleName(
      `${en.settings.who}, ${en.settings.whoCustom}`,
    );
  });
});

describe('who gets through', () => {
  it('explains a refused change', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('who');

    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });
    await fireEvent.press(view.getByTestId('who-apply'));

    expect(await view.findByTestId('settings-problem')).toBeOnTheScreen();
  });

  it('draws the two lanes and asks nothing when calls already follow them', async () => {
    runningBackend({ startAt: null, preferences: LANES });
    const view = await openPage('who');
    expect(view.getByTestId('lane-contacts')).toBeOnTheScreen();
    expect(view.getByTestId('lane-unknown')).toBeOnTheScreen();
    expect(view.queryByTestId('who-apply')).toBeNull();
  });

  it('offers the lanes to somebody whose calls were set up another way', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('who');

    expect(view.getByTestId('who-differs')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('who-apply'));
    await savedOnce(backend, 0);
    expect(backend.preferences().call_handling).toEqual(LANES.call_handling);
    await waitFor(() => {
      expect(view.queryByTestId('who-apply')).toBeNull();
    });
  });
});

describe("when you're called", () => {
  it('turns every-call on as both of the flags it stands for', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('when');

    expect(view.getByTestId('call-graph')).toBeOnTheScreen();
    await fireEvent(view.getByTestId('when-every-call'), 'valueChange', true);
    await savedOnce(backend, 0);
    expect(backend.preferences().notifications).toMatchObject({
      on_handled_call: true,
      on_blocked_call: true,
    });
  });

  it('saves the evening round-up and leaves the rest alone', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('when');

    await fireEvent(view.getByTestId('when-evening'), 'valueChange', true);
    await savedOnce(backend, 0);
    expect(backend.preferences().notifications).toEqual({
      ...DEFAULT_PREFERENCES.notifications,
      daily_summary: true,
    });
  });

  it('puts a refused change back and says why', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('when');

    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });
    await fireEvent(view.getByTestId('when-evening'), 'valueChange', true);

    expect(await view.findByTestId('settings-problem')).toHaveTextContent(
      en.common.saveFailed,
    );
    expect(view.getByTestId('when-evening').props.value).toBe(false);
    expect(backend.preferences().notifications.daily_summary).toBe(false);
  });
});

describe('hours', () => {
  it('shows the full ring when the assistant answers around the clock', async () => {
    runningBackend({ startAt: null });
    const view = await openPage('hours');
    expect(view.getByTestId('hours-always')).toBeOnTheScreen();
    expect(view.getByTestId('hours-mode-always')).toBeChecked();
    expect(view.queryByTestId('hours-from')).toBeNull();
  });

  it('sets hours, saving each change as it is made', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('hours');

    await fireEvent.press(view.getByTestId('hours-mode-set'));
    await savedOnce(backend, 0);
    expect(backend.preferences().hours.active).toMatchObject({
      start: '09:00',
      end: '17:00',
    });
    expect(await view.findByTestId('hours-window')).toBeOnTheScreen();
    expect(view.getByText(en.hours.outside)).toBeOnTheScreen();

    await fireEvent.press(view.getByTestId('hours-from'));
    await fireEvent.press(view.getByTestId('hours-sheet-09:30'));
    await savedOnce(backend, 1);
    expect(backend.preferences().hours.active).toMatchObject({
      start: '09:30',
      end: '17:00',
    });
  });

  it('does not offer the time the other end is already at', async () => {
    const backend = runningBackend({
      startAt: null,
      preferences: {
        ...DEFAULT_PREFERENCES,
        hours: {
          active: { start: '09:00', end: '10:00', zone: 'Europe/London' },
        },
      },
    });
    const view = await openPage('hours');
    expect(view.getByTestId('hours-figure')).toHaveTextContent('1 hr');

    await fireEvent.press(view.getByTestId('hours-to'));
    expect(view.getByTestId('hours-sheet-09:00')).toBeDisabled();
    await fireEvent.press(view.getByTestId('hours-sheet-close'));
    expect(backend.patches).toEqual([]);
  });

  it('goes back to around the clock', async () => {
    const backend = runningBackend({
      startAt: null,
      preferences: {
        ...DEFAULT_PREFERENCES,
        hours: {
          active: { start: '22:00', end: '07:00', zone: 'Europe/London' },
        },
      },
    });
    const view = await openPage('hours');

    await fireEvent.press(view.getByTestId('hours-mode-always'));
    await savedOnce(backend, 0);
    expect(backend.preferences().hours).toEqual({ active: null });
    expect(await view.findByTestId('hours-always')).toBeOnTheScreen();
  });

  it('puts a refused change back', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('hours');

    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });
    await fireEvent.press(view.getByTestId('hours-mode-set'));

    expect(await view.findByTestId('settings-problem')).toBeOnTheScreen();
    expect(view.getByTestId('hours-always')).toBeOnTheScreen();
  });
});

describe('what it may do', () => {
  it('reads no capabilities as none granted, and explains a refused grant', async () => {
    const backend = runningBackend({
      startAt: null,
      preferences: { ...DEFAULT_PREFERENCES, authority: {} },
    });
    const view = await openPage('authority');
    expect(view.getByTestId('capability-take_a_message').props.value).toBe(
      false,
    );

    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });
    await fireEvent(
      view.getByTestId('capability-take_a_message'),
      'valueChange',
      true,
    );

    expect(await view.findByTestId('settings-problem')).toBeOnTheScreen();
  });

  it('grants and takes back one capability at a time', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('authority');

    await fireEvent(
      view.getByTestId('capability-confirm_appointments'),
      'valueChange',
      true,
    );
    await savedOnce(backend, 0);
    expect(backend.preferences().authority.capabilities).toEqual([
      'confirm_appointments',
    ]);

    await fireEvent(
      view.getByTestId('capability-confirm_appointments'),
      'valueChange',
      false,
    );
    await savedOnce(backend, 1);
    expect(backend.preferences().authority.capabilities).toEqual([]);
  });
});

describe('what it may say', () => {
  it('adds a fact without touching the rest of the personality', async () => {
    const backend = runningBackend({
      startAt: null,
      preferences: {
        ...DEFAULT_PREFERENCES,
        personality: {
          formality: 'warm',
          verbosity: 'brief',
          topics: ['school'],
        },
      },
    });
    const view = await openPage('say');

    expect(view.getByTestId('say-empty')).toBeOnTheScreen();
    await fireEvent.changeText(
      view.getByTestId('say-input'),
      'Parcels go to the gate',
    );
    await fireEvent.press(view.getByTestId('say-add'));
    await savedOnce(backend, 0);

    expect(backend.preferences().personality).toEqual({
      formality: 'warm',
      verbosity: 'brief',
      topics: ['school'],
      disclosable_facts: ['Parcels go to the gate'],
    });
    expect(await view.findByText('“Parcels go to the gate”')).toBeOnTheScreen();
  });

  it('refuses a duplicate before sending it', async () => {
    const backend = runningBackend({
      startAt: null,
      preferences: {
        ...DEFAULT_PREFERENCES,
        personality: {
          ...DEFAULT_PREFERENCES.personality,
          disclosable_facts: ['Free after six'],
        },
      },
    });
    const view = await openPage('say');

    await fireEvent.changeText(view.getByTestId('say-input'), 'free after six');
    await fireEvent.press(view.getByTestId('say-add'));

    expect(view.getByText(en.say.duplicate)).toBeOnTheScreen();
    expect(backend.patches).toEqual([]);
  });

  it('removes a fact', async () => {
    const backend = runningBackend({
      startAt: null,
      preferences: {
        ...DEFAULT_PREFERENCES,
        personality: {
          ...DEFAULT_PREFERENCES.personality,
          disclosable_facts: ['Free after six'],
        },
      },
    });
    const view = await openPage('say');

    await fireEvent.press(view.getByTestId('say-remove-0'));
    await savedOnce(backend, 0);
    expect(backend.preferences().personality.disclosable_facts).toEqual([]);
  });
});

describe('personalise', () => {
  it('saves manner and length as pictures are tapped', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('personalise');

    await fireEvent.press(view.getByTestId('personality-formality-formal'));
    await savedOnce(backend, 0);
    await fireEvent.press(view.getByTestId('personality-verbosity-detailed'));
    await savedOnce(backend, 1);

    expect(backend.preferences().personality).toMatchObject({
      formality: 'formal',
      verbosity: 'detailed',
    });
    expect(view.getByTestId('personality-verbosity-detailed')).toBeChecked();
  });

  it('refuses a topic that is a sentence before sending it', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('personalise');
    await fireEvent.press(view.getByTestId('personalise-open-topics'));
    await view.findByTestId('settings-topics');

    await fireEvent.changeText(view.getByTestId('topic-input'), 'x'.repeat(61));
    await fireEvent.press(view.getByTestId('topic-add'));

    expect(
      view.getByText(en.preferences.personality.invalidTopic),
    ).toBeOnTheScreen();
    expect(backend.patches).toEqual([]);
  });

  it('opens topics, adds one normalised, and removes it', async () => {
    const backend = runningBackend({ startAt: null });
    const view = await openPage('personalise');

    await fireEvent.press(view.getByTestId('personalise-open-topics'));
    expect(await view.findByTestId('settings-topics')).toBeOnTheScreen();

    await fireEvent.changeText(
      view.getByTestId('topic-input'),
      '  The   School Run ',
    );
    await fireEvent.press(view.getByTestId('topic-add'));
    await savedOnce(backend, 0);
    expect(backend.preferences().personality.topics).toEqual([
      'the school run',
    ]);

    await fireEvent.press(
      await view.findByTestId('topic-remove-the school run'),
    );
    await savedOnce(backend, 1);
    expect(backend.preferences().personality.topics).toEqual([]);
  });
});
