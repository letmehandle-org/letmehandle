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

import type { paths } from '@letmehandle/api-client';

import { App } from '../App';
import { en } from '../i18n/locales/en';
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
      voices: [
        { id: 'juniper', name: 'Juniper', locales: ['en'], previewable: false },
      ],
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

  it('offers a retry rather than an empty list when the voices cannot be loaded, and takes it', async () => {
    withVoices();
    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    await fireEvent.press(view.getByTestId('open-settings'));

    const working = globalThis.fetch;
    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;
    await fireEvent.press(view.getByTestId('settings-open-voice'));

    expect(await view.findByText(en.voice.loadFailed)).toBeOnTheScreen();

    // A retry button that does nothing is worse than none: it is the only thing somebody can
    // press, and it teaches them the screen is broken rather than the connection.
    globalThis.fetch = working;
    await fireEvent.press(view.getByTestId('voice-retry'));

    expect(await view.findByTestId('voice-option-briar')).toBeOnTheScreen();
    expect(view.queryByText(en.voice.loadFailed)).toBeNull();
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

  it('says plainly when the chosen voice has been withdrawn', async () => {
    // The provider no longer offers it, so the fallback chain has stepped past it. Silence here
    // means finding out from a caller; a vaguer message means guessing why.
    const backend = withVoices({ persona: 'briar' });
    backend.withdrawVoice('briar');
    const view = await openVoice();

    expect(
      view.getByText(en.voice.withdrawn.replace('{{voice}}', 'Ash')),
    ).toBeOnTheScreen();
  });

  it('does not report a cloned voice that is working as a problem', async () => {
    // A clone outranks a chosen voice. Comparing the answering voice with the chosen one alone
    // would tell somebody whose clone is doing its job that their choice is being ignored.
    withVoices({ cloned: 'cove', persona: 'briar' });
    const view = await openVoice();

    expect(view.getByText(inUse('Cove'))).toBeOnTheScreen();
  });

  it('says a cloned voice is not answering when the chain has stepped past it', async () => {
    const backend = withVoices({ cloned: 'cove', persona: 'briar' });
    backend.withdrawVoice('cove');
    const view = await openVoice();

    // Not "withdrawn": a cloned voice is never in the catalogue, so its absence proves nothing
    // about why it is not being used.
    expect(
      view.getByText(en.voice.notYourChoice.replace('{{voice}}', 'Briar')),
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
    //
    // The annotation below is the trip-wire. The day the backend registers a preview route,
    // the generated schema gains the path, `false` stops compiling, and this test — and the
    // screen — have to be written against a route that exists.
    const schemaHasPreview: '/v1/voices/{voice_id}/preview' extends keyof paths
      ? true
      : false = false;
    expect(schemaHasPreview).toBe(false);

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
