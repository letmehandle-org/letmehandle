/**
 * Choosing what the assistant sounds like.
 *
 * Two things are worth testing hardest. What a call would actually use, because the chosen
 * voice and the answering voice differ exactly when something has gone wrong and that is the
 * moment somebody needs to be told. And what is not on the screen: a control for a capability
 * the provider does not have is the failure D-009 exists to prevent, and absence is only ever
 * protected by a test that asserts it.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { canPreviewVoices } from '../voice/preview';
import {
  DEFAULT_VOICE_ID,
  VOICE_CAPABILITIES,
  VOICES,
  runningBackend,
  type RunningBackend,
  type VoiceSetup,
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

/** The option that hands the choice back to the provider carries no id, as the wire has none. */
const LEAVE_TO_THE_ASSISTANT = 'voice-option-';

async function openVoice(): Promise<View> {
  const view = await render(<App />);
  await waitFor(() => {
    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
  });
  await fireEvent.press(view.getByTestId('open-settings'));
  await fireEvent.press(view.getByTestId('settings-open-voice'));
  await waitFor(() => {
    expect(view.getByTestId('voice-option-ash')).toBeOnTheScreen();
  });
  return view;
}

function withVoices(voice?: VoiceSetup): RunningBackend {
  return runningBackend({ voice });
}

function inUse(voiceName: string): string {
  return en.voice.inUse.replace('{{voice}}', voiceName);
}

describe('the voices on offer', () => {
  it('lists what the server offers, rather than a list shipped with the app', async () => {
    withVoices({
      voices: [{ id: 'juniper', name: 'Juniper', locales: ['en'] }],
    });
    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    await fireEvent.press(view.getByTestId('open-settings'));
    await fireEvent.press(view.getByTestId('settings-open-voice'));

    expect(await view.findByText('Juniper')).toBeOnTheScreen();
    for (const voice of VOICES) {
      expect(view.queryByText(voice.name)).toBeNull();
    }
  });

  it('offers leaving the choice to the assistant alongside the voices', async () => {
    withVoices();
    const view = await openVoice();

    expect(view.getByTestId(LEAVE_TO_THE_ASSISTANT)).toBeOnTheScreen();
    for (const voice of VOICES) {
      expect(view.getByTestId(`voice-option-${voice.id}`)).toBeOnTheScreen();
    }
  });

  it('offers a retry rather than an empty list when the voices cannot be loaded', async () => {
    withVoices();
    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    await fireEvent.press(view.getByTestId('open-settings'));

    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;
    await fireEvent.press(view.getByTestId('settings-open-voice'));

    expect(await view.findByText(en.voice.loadFailed)).toBeOnTheScreen();
    expect(view.getByTestId('voice-retry')).toBeOnTheScreen();
  });
});

describe('choosing one', () => {
  it('saves the choice and says it was kept', async () => {
    const backend = withVoices();
    const view = await openVoice();

    await fireEvent.press(view.getByTestId('voice-option-briar'));
    await fireEvent.press(view.getByTestId('voice-save'));

    expect(await view.findByText(en.voice.saved)).toBeOnTheScreen();
    expect(backend.chosenVoice()).toBe('briar');
    expect(view.getByText(inUse('Briar'))).toBeOnTheScreen();
  });

  it('clears the choice, and says the default is answering again', async () => {
    const backend = withVoices({ persona: 'briar' });
    const view = await openVoice();

    await fireEvent.press(view.getByTestId(LEAVE_TO_THE_ASSISTANT));
    await fireEvent.press(view.getByTestId('voice-save'));

    await waitFor(() => {
      expect(backend.chosenVoice()).toBeNull();
    });
    expect(view.getByText(inUse('Ash'))).toBeOnTheScreen();
  });
});

describe('what is actually answering calls', () => {
  it('names the voice in use when nothing has been chosen', async () => {
    // Somebody who has chosen nothing still has a voice answering for them, and being unable to
    // find out which is the thing this line exists to prevent.
    withVoices();
    const view = await openVoice();

    expect(DEFAULT_VOICE_ID).toBe('ash');
    expect(view.getByText(inUse('Ash'))).toBeOnTheScreen();
  });

  it('says so when the chosen voice is not the one answering', async () => {
    // The chosen voice was withdrawn by the provider, so the fallback chain has stepped past it.
    // Silence here means finding out from a caller.
    const backend = withVoices({ persona: 'briar' });
    backend.withdrawVoice('briar');
    const view = await openVoice();

    expect(
      view.getByText(en.voice.notYourChoice.replace('{{voice}}', 'Ash')),
    ).toBeOnTheScreen();
  });
});

describe('what this provider cannot do', () => {
  it('draws no preview control, because there are no samples to play', async () => {
    withVoices();
    const view = await openVoice();

    expect(VOICE_CAPABILITIES.preview).toBe(false);
    for (const voice of VOICES) {
      expect(view.queryByTestId(`voice-preview-${voice.id}`)).toBeNull();
    }
    expect(view.queryByText(/preview|\blisten\b|\bplay\b|sample/i)).toBeNull();
  });

  it('draws none even where a provider says it has samples, having no way to fetch one', async () => {
    // Both halves have to agree. A provider that declares preview against a client with no
    // route for it can no more play a sample than one that declares none, and a control drawn
    // on the declaration alone would be a button that 404s.
    expect(canPreviewVoices({ ...VOICE_CAPABILITIES, preview: true })).toBe(
      false,
    );

    withVoices({ capabilities: { ...VOICE_CAPABILITIES, preview: true } });
    const view = await openVoice();

    expect(view.queryByText(/preview|\blisten\b|\bplay\b|sample/i)).toBeNull();
  });

  it('offers no way to train or clone a voice', async () => {
    withVoices();
    const view = await openVoice();

    expect(VOICE_CAPABILITIES.cloning).toBe(false);
    expect(VOICE_CAPABILITIES.custom_voice).toBe(false);
    expect(view.queryByTestId('voice-clone')).toBeNull();
    expect(view.queryByText(/clone|train|record|your own voice/i)).toBeNull();
  });
});

describe('when the server refuses the choice', () => {
  it('puts the previous voice back and says why, rather than letting it vanish', async () => {
    // The provider withdrew the voice after this client read the catalogue. The screen is still
    // offering it, and the 422 is the first anybody hears of it.
    const backend = withVoices({ persona: 'ash' });
    const view = await openVoice();
    backend.withdrawVoice('cove');

    await fireEvent.press(view.getByTestId('voice-option-cove'));
    await fireEvent.press(view.getByTestId('voice-save'));

    expect(await view.findByText(en.voice.saveFailed)).toBeOnTheScreen();
    expect(backend.chosenVoice()).toBe('ash');
    expect(
      view.getByTestId('voice-option-ash').props.accessibilityState.selected,
    ).toBe(true);
    expect(
      view.getByTestId('voice-option-cove').props.accessibilityState.selected,
    ).toBe(false);
  });

  it('says plainly when the service cannot be reached', async () => {
    withVoices();
    const view = await openVoice();

    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    await fireEvent.press(view.getByTestId('voice-option-briar'));
    await fireEvent.press(view.getByTestId('voice-save'));

    expect(await view.findByText(en.common.noConnection)).toBeOnTheScreen();
  });
});
