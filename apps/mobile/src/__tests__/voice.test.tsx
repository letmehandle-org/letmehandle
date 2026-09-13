/**
 * Choosing what the assistant sounds like, on the personalise page.
 *
 * Two things are tested hardest. What a call would actually use, because the chosen voice and
 * the answering voice differ exactly when something has gone wrong. And what is not on the
 * screen: a control for a capability the provider does not have is the failure D-009 exists to
 * prevent, and absence is only protected by a test that asserts it.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import type { paths } from '@letmehandle/api-client';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import {
  VOICE_CAPABILITIES,
  VOICES,
  runningBackend,
  type RunningBackend,
  type VoiceSetup,
} from './support/backend';
import { jsonResponse } from './support/http';

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

async function openVoices(): Promise<View> {
  const view = await render(<App />);
  await waitFor(() => {
    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
  });
  await fireEvent.press(view.getByTestId('tab-settings'));
  await fireEvent.press(view.getByTestId('settings-open-personalise'));
  await waitFor(() => {
    expect(view.getByTestId('voice-option-ash')).toBeOnTheScreen();
  });
  return view;
}

function withVoices(voice?: VoiceSetup): RunningBackend {
  return runningBackend({ startAt: null, voice });
}

describe('choosing a voice', () => {
  it('offers every voice on the catalogue and marks the one calls are answered in', async () => {
    withVoices();
    const view = await openVoices();

    for (const voice of VOICES) {
      expect(view.getByTestId(`voice-option-${voice.id}`)).toHaveAccessibleName(
        voice.name,
      );
    }
    // Nothing chosen, so the provider's default answers, and that is what is marked.
    expect(view.getByTestId('voice-option-ash')).toBeChecked();
  });

  it('saves a tapped voice and marks it', async () => {
    const backend = withVoices();
    const view = await openVoices();

    await fireEvent.press(view.getByTestId('voice-option-briar'));

    await waitFor(() => {
      expect(backend.chosenVoice()).toBe('briar');
    });
    await waitFor(() => {
      expect(view.getByTestId('voice-option-briar')).toBeChecked();
    });
  });

  it('says so when calls are answered in a voice that was not chosen', async () => {
    withVoices({ persona: 'juniper', voices: VOICES });
    const view = await openVoices();

    expect(view.getByTestId('voice-not-yours')).toHaveTextContent(
      en.personalise.voiceNotYourChoice.replace('{{voice}}', 'Ash'),
    );
  });

  it('keeps the voice in use and explains when the server refuses the choice', async () => {
    const backend = withVoices();
    const view = await openVoices();

    backend.withdrawVoice('cove');
    await fireEvent.press(view.getByTestId('voice-option-cove'));

    expect(await view.findByTestId('voice-problem')).toHaveTextContent(
      en.personalise.voiceSaveFailed,
    );
    expect(backend.chosenVoice()).toBeNull();
    expect(view.getByTestId('voice-option-ash')).toBeChecked();
  });
});

describe('what this provider cannot do', () => {
  it('draws no preview control, because there are no samples to play', async () => {
    withVoices();
    const view = await openVoices();

    expect(VOICE_CAPABILITIES.preview).toBe(false);
    for (const voice of VOICES) {
      expect(view.queryByTestId(`voice-preview-${voice.id}`)).toBeNull();
    }
    expect(view.queryByText(/preview|\blisten\b|\bplay\b|sample/i)).toBeNull();
  });

  it('draws none even where a provider says it has samples, having no way to fetch one', async () => {
    // The annotation below is the trip-wire. The day the backend registers a preview route,
    // the generated schema gains the path, `false` stops compiling, and this test — and the
    // screen — have to be written against a route that exists.
    const schemaHasPreview: '/v1/voices/{voice_id}/preview' extends keyof paths
      ? true
      : false = false;
    expect(schemaHasPreview).toBe(false);

    withVoices({ capabilities: { ...VOICE_CAPABILITIES, preview: true } });
    const view = await openVoices();

    expect(view.queryByText(/preview|\blisten\b|\bplay\b|sample/i)).toBeNull();
  });

  it('offers no way to train or clone a voice', async () => {
    withVoices();
    const view = await openVoices();

    expect(VOICE_CAPABILITIES.cloning).toBe(false);
    expect(VOICE_CAPABILITIES.custom_voice).toBe(false);
    expect(view.queryByTestId('voice-clone')).toBeNull();
    expect(view.queryByText(/clone|train|record|your own voice/i)).toBeNull();
  });
});

describe('when the voices cannot be loaded', () => {
  it('says so and offers another try', async () => {
    runningBackend({ startAt: null });
    const original = globalThis.fetch;
    let failed = false;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      if (!failed && url.endsWith('/v1/voices')) {
        failed = true;
        return jsonResponse(503, { error: 'unavailable', message: 'x' });
      }
      return original(url, init);
    }) as typeof fetch;

    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    await fireEvent.press(view.getByTestId('tab-settings'));
    await fireEvent.press(view.getByTestId('settings-open-personalise'));

    expect(await view.findByTestId('voice-problem')).toHaveTextContent(
      en.personalise.voiceUnavailable,
    );
    await fireEvent.press(view.getByTestId('voice-retry'));
    expect(await view.findByTestId('voice-option-ash')).toBeOnTheScreen();
  });
});
