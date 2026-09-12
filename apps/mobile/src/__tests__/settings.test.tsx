/**
 * Changing an answer after setup is over.
 *
 * Nothing in this product is set once, so the test that matters most is the dull one: every
 * section reachable, and every one of them saving.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import type { PreferenceSection } from '../preferences/options';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { PREFERENCE_SECTIONS } from '../preferences/options';
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

async function openSettings(): Promise<View> {
  const view = await render(<App />);
  await waitFor(() => {
    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
  });
  await fireEvent.press(view.getByTestId('open-settings'));
  await waitFor(() => {
    expect(view.getByTestId('settings-screen')).toBeOnTheScreen();
  });
  return view;
}

async function openSection(section: PreferenceSection): Promise<View> {
  const view = await openSettings();
  await fireEvent.press(view.getByTestId(`settings-open-${section}`));
  await waitFor(() => {
    expect(view.getByTestId(`settings-${section}`)).toBeOnTheScreen();
  });
  return view;
}

async function save(view: View, backend: RunningBackend): Promise<void> {
  const before = backend.patches.length;
  await fireEvent.press(view.getByTestId('settings-save'));
  await waitFor(() => {
    expect(backend.patches.length).toBeGreaterThan(before);
  });
}

describe('getting to the settings', () => {
  it('lists every section, including the ones setup let somebody skip', async () => {
    runningBackend();
    const view = await openSettings();

    for (const section of PREFERENCE_SECTIONS) {
      expect(view.getByTestId(`settings-open-${section}`)).toBeOnTheScreen();
    }
  });

  it.each(PREFERENCE_SECTIONS)('opens %s and can save it', async section => {
    const backend = runningBackend();
    const view = await openSection(section);

    await save(view, backend);

    expect(await view.findByText(en.settings.saved)).toBeOnTheScreen();
  });
});

describe('how calls are handled', () => {
  it('blocks a category rather than giving it a posture as well', async () => {
    // The backend refuses a category that is in both lists. One control per category is what
    // makes that contradiction impossible to express.
    const backend = runningBackend();
    const view = await openSection('call_handling');

    await fireEvent.press(view.getByTestId('handling-category-sales-blocked'));
    await save(view, backend);

    const handling = backend.preferences().call_handling;
    expect(handling.blocked_categories).toEqual(['sales']);
    expect(handling.posture_by_category).toEqual({});
  });

  it('gives a category its own posture', async () => {
    const backend = runningBackend();
    const view = await openSection('call_handling');

    await fireEvent.press(
      view.getByTestId('handling-category-healthcare-pass_through'),
    );
    await fireEvent.press(view.getByTestId('handling-escalate-20'));
    await save(view, backend);

    const handling = backend.preferences().call_handling;
    expect(handling.posture_by_category).toEqual({
      healthcare: 'pass_through',
    });
    expect(handling.blocked_categories).toEqual([]);
    expect(handling.escalate_at_or_above).toBe(20);
  });
});

describe('important contacts', () => {
  it('adds one, and saves the whole list rather than the addition', async () => {
    const backend = runningBackend();
    const view = await openSection('important_contacts');

    expect(view.getByTestId('contacts-empty')).toBeOnTheScreen();

    await fireEvent.changeText(view.getByTestId('contact-label'), 'School');
    await fireEvent.changeText(
      view.getByTestId('contact-number'),
      '+1 (202) 555-0143',
    );
    await fireEvent.press(view.getByTestId('contact-posture-reject'));
    await fireEvent.press(view.getByTestId('contact-add'));
    await save(view, backend);

    expect(backend.preferences().important_contacts).toEqual([
      { label: 'School', phone_number: '+12025550143', posture: 'reject' },
    ]);
  });

  it('refuses a number already on the list, however it was typed', async () => {
    const backend = runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        important_contacts: [
          {
            label: 'School',
            phone_number: '+12025550143',
            posture: 'pass_through',
          },
        ],
      },
    });
    const view = await openSection('important_contacts');

    await fireEvent.changeText(
      view.getByTestId('contact-label'),
      'School again',
    );
    await fireEvent.changeText(
      view.getByTestId('contact-number'),
      '+1 (202) 555-0143',
    );
    await fireEvent.press(view.getByTestId('contact-add'));

    expect(
      view.getByText(en.preferences.important_contacts.duplicate),
    ).toBeOnTheScreen();
    expect(backend.patches).toHaveLength(0);
  });

  it('says what is wrong with a number it cannot use', async () => {
    runningBackend();
    const view = await openSection('important_contacts');

    await fireEvent.changeText(view.getByTestId('contact-label'), 'School');
    await fireEvent.changeText(view.getByTestId('contact-number'), '5550143');
    await fireEvent.press(view.getByTestId('contact-add'));

    expect(
      view.getByText(en.preferences.important_contacts.invalidNumber),
    ).toBeOnTheScreen();
  });

  it('removes one', async () => {
    const backend = runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        important_contacts: [
          {
            label: 'School',
            phone_number: '+12025550143',
            posture: 'pass_through',
          },
        ],
      },
    });
    const view = await openSection('important_contacts');

    await fireEvent.press(view.getByTestId('contact-remove-+12025550143'));
    await save(view, backend);

    expect(backend.preferences().important_contacts).toEqual([]);
  });
});

describe('hours', () => {
  it('saves a window that runs past midnight', async () => {
    const backend = runningBackend();
    const view = await openSection('hours');

    await fireEvent(view.getByTestId('hours-quiet-on'), 'valueChange', true);
    await fireEvent.changeText(
      view.getByTestId('hours-quiet-zone'),
      'Europe/London',
    );
    await save(view, backend);

    expect(backend.preferences().hours.quiet).toEqual({
      start: '22:00',
      end: '07:00',
      zone: 'Europe/London',
    });
  });

  it('will not save a time it cannot read, and says why', async () => {
    const backend = runningBackend();
    const view = await openSection('hours');

    await fireEvent(view.getByTestId('hours-working-on'), 'valueChange', true);
    await fireEvent.changeText(view.getByTestId('hours-working-end'), '25:00');

    expect(view.getByText(en.preferences.hours.invalidTime)).toBeOnTheScreen();

    await fireEvent.press(view.getByTestId('settings-save'));
    expect(backend.patches).toHaveLength(0);
  });

  it('will not save a window that covers no time', async () => {
    const backend = runningBackend();
    const view = await openSection('hours');

    await fireEvent(view.getByTestId('hours-working-on'), 'valueChange', true);
    await fireEvent.changeText(
      view.getByTestId('hours-working-start'),
      '09:00',
    );
    await fireEvent.changeText(view.getByTestId('hours-working-end'), '09:00');

    expect(view.getByText(en.preferences.hours.emptyWindow)).toBeOnTheScreen();
    expect(backend.patches).toHaveLength(0);
  });

  it('sends the handling alongside, so saving hours cannot reset it', async () => {
    const backend = runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        call_handling: {
          ...DEFAULT_PREFERENCES.call_handling,
          default_posture: 'reject',
        },
      },
    });
    const view = await openSection('hours');

    await fireEvent(view.getByTestId('hours-quiet-on'), 'valueChange', true);
    await save(view, backend);

    expect(backend.preferences().call_handling.default_posture).toBe('reject');
  });
});

describe('what the assistant may do', () => {
  it('grants a capability', async () => {
    const backend = runningBackend();
    const view = await openSection('authority');

    await fireEvent(
      view.getByTestId('capability-take_a_message'),
      'valueChange',
      true,
    );
    await save(view, backend);

    expect(backend.preferences().authority.capabilities).toEqual([
      'take_a_message',
    ]);
  });

  it('takes one back', async () => {
    const backend = runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        authority: { capabilities: ['take_a_message'] },
      },
    });
    const view = await openSection('authority');

    await fireEvent(
      view.getByTestId('capability-take_a_message'),
      'valueChange',
      false,
    );
    await save(view, backend);

    expect(backend.preferences().authority.capabilities).toEqual([]);
  });
});

describe('personality', () => {
  it('adds a topic, folded to one spelling', async () => {
    const backend = runningBackend();
    const view = await openSection('personality');

    expect(view.getByTestId('topics-empty')).toBeOnTheScreen();

    await fireEvent.changeText(
      view.getByTestId('topic-input'),
      '  School   Run ',
    );
    await fireEvent.press(view.getByTestId('topic-add'));
    await fireEvent.press(view.getByTestId('personality-formality-warm'));
    await save(view, backend);

    expect(backend.preferences().personality).toEqual({
      formality: 'warm',
      verbosity: 'normal',
      topics: ['school run'],
    });
  });

  it('refuses a topic already on the list', async () => {
    runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        personality: {
          formality: 'neutral',
          verbosity: 'normal',
          topics: ['school run'],
        },
      },
    });
    const view = await openSection('personality');

    await fireEvent.changeText(view.getByTestId('topic-input'), 'School Run');
    await fireEvent.press(view.getByTestId('topic-add'));

    expect(
      view.getByText(en.preferences.personality.duplicateTopic),
    ).toBeOnTheScreen();
  });

  it('removes a topic', async () => {
    const backend = runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        personality: {
          formality: 'neutral',
          verbosity: 'normal',
          topics: ['school run'],
        },
      },
    });
    const view = await openSection('personality');

    await fireEvent.press(view.getByTestId('topic-remove-school run'));
    await fireEvent.press(view.getByTestId('personality-verbosity-brief'));
    await save(view, backend);

    expect(backend.preferences().personality.topics).toEqual([]);
  });
});

describe('reading what is already stored', () => {
  it('shows a window that was set, rather than the suggestion', async () => {
    runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        hours: {
          working: { start: '08:15', end: '16:45', zone: 'Europe/Lisbon' },
          quiet: null,
        },
      },
    });
    const view = await openSection('hours');

    expect(view.getByTestId('hours-working-start').props.value).toBe('08:15');
    expect(view.getByTestId('hours-working-zone').props.value).toBe(
      'Europe/Lisbon',
    );
  });

  it('copes with the optional lists the wire format allows to be absent', async () => {
    // `capabilities`, `topics`, `posture_by_category` and `blocked_categories` are all optional
    // in the schema. A section that assumed them present would crash on the first user whose
    // stored preferences predate the field.
    const backend = runningBackend({
      preferences: {
        ...DEFAULT_PREFERENCES,
        call_handling: {
          default_posture: 'handle_with_agent',
          anonymous_posture: 'reject',
          escalate_at_or_above: 40,
        },
        authority: {},
        personality: { formality: 'neutral', verbosity: 'normal' },
      },
    });

    const handling = await openSection('call_handling');
    await save(handling, backend);
    expect(backend.preferences().call_handling.blocked_categories).toEqual([]);

    const authority = await openSection('authority');
    expect(authority.getByTestId('capability-take_a_message').props.value).toBe(
      false,
    );

    const personality = await openSection('personality');
    expect(personality.getByTestId('topics-empty')).toBeOnTheScreen();
  });
});

describe('when the server refuses a change', () => {
  it('says so, and puts the control back where it was', async () => {
    // A change that vanishes with no explanation reads as the application losing work, so the
    // rollback and the message go together or neither is worth having.
    const backend = runningBackend();
    backend.refuseNextSave({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    });
    const view = await openSection('notifications');

    await fireEvent(
      view.getByTestId('notification-daily_summary'),
      'valueChange',
      true,
    );
    await save(view, backend);

    expect(await view.findByText(en.settings.saveFailed)).toBeOnTheScreen();
    expect(view.getByTestId('notification-daily_summary').props.value).toBe(
      false,
    );
    expect(backend.preferences().notifications.daily_summary).toBe(false);
  });

  it('says plainly when the service cannot be reached', async () => {
    runningBackend();
    const view = await openSection('notifications');

    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    await fireEvent.press(view.getByTestId('settings-save'));

    expect(await view.findByText(en.common.noConnection)).toBeOnTheScreen();
  });
});
