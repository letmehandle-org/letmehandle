/** Holding preferences: optimistic saves, refusals and loading. */
import {
  act,
  fireEvent,
  render,
  renderHook,
  waitFor,
} from '@testing-library/react-native';
import React from 'react';
import { Text } from 'react-native';

import type { Preferences } from '@letmehandle/api-client';

import { SessionProvider } from '../auth/SessionProvider';
import { initialiseI18n } from '../i18n';
import { en } from '../i18n/locales/en';
import {
  PreferencesProvider,
  usePreferences,
} from '../preferences/PreferencesProvider';
import { useImmediateSave } from '../preferences/useImmediateSave';
import {
  DEFAULT_PREFERENCES,
  ONBOARDING_COMPLETE,
  onboardingAt,
} from './support/backend';
import { jsonResponse } from './support/http';

const PROFILE = {
  id: 'u1',
  phone_number: '+12025550143',
  display_name: null,
  locale: 'en',
};

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

const WARMER: Preferences = {
  ...DEFAULT_PREFERENCES,
  personality: { formality: 'warm', verbosity: 'brief', topics: [] },
};

/** A backend whose PATCH is held open until the test lets it finish. */
function backendHoldingPatch(patch: () => { status: number; body: unknown }): {
  release: () => void;
  patched: () => boolean;
} {
  let letGo: (() => void) | null = null;
  let asked = false;

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    const path = url.replace(/^https?:\/\/[^/]+/, '');

    if (path === '/v1/preferences' && init?.method === 'PATCH') {
      asked = true;
      await new Promise<void>(resolve => {
        letGo = resolve;
      });
      const reply = patch();
      return jsonResponse(reply.status, reply.body);
    }

    const bodies: Record<string, unknown> = {
      '/v1/me': PROFILE,
      '/v1/preferences': DEFAULT_PREFERENCES,
      '/v1/onboarding': ONBOARDING_COMPLETE,
    };
    return jsonResponse(200, bodies[path]);
  }) as unknown as typeof fetch;

  return {
    release: () => {
      letGo?.();
    },
    patched: () => asked,
  };
}

function wrapper({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <SessionProvider>
      <PreferencesProvider>{children}</PreferencesProvider>
    </SessionProvider>
  );
}

async function loaded() {
  const view = await renderHook(() => usePreferences(), { wrapper });
  await waitFor(() => {
    expect(view.result.current).toBeDefined();
  });
  return view;
}

beforeAll(async () => {
  await initialiseI18n('en');
});

describe('loading', () => {
  it('holds the application back until there is something to render', async () => {
    let letGo: (() => void) | null = null;
    globalThis.fetch = (async (url: string) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (path === '/v1/preferences') {
        await new Promise<void>(resolve => {
          letGo = resolve;
        });
      }
      const bodies: Record<string, unknown> = {
        '/v1/me': PROFILE,
        '/v1/preferences': DEFAULT_PREFERENCES,
        '/v1/onboarding': ONBOARDING_COMPLETE,
      };
      return jsonResponse(200, bodies[path]);
    }) as unknown as typeof fetch;

    const view = await render(
      wrapper({ children: <Text>the application</Text> }),
    );

    expect(view.getByTestId('preferences-loading')).toBeOnTheScreen();
    expect(view.queryByText('the application')).toBeNull();

    await act(async () => {
      letGo?.();
    });

    expect(await view.findByText('the application')).toBeOnTheScreen();
  });

  it('offers another go when they cannot be read, and takes it', async () => {
    let attempts = 0;
    globalThis.fetch = (async (url: string) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (path === '/v1/preferences') {
        attempts += 1;
        if (attempts === 1) {
          throw new TypeError('Network request failed');
        }
      }
      const bodies: Record<string, unknown> = {
        '/v1/me': PROFILE,
        '/v1/preferences': DEFAULT_PREFERENCES,
        '/v1/onboarding': ONBOARDING_COMPLETE,
      };
      return jsonResponse(200, bodies[path]);
    }) as unknown as typeof fetch;

    const view = await render(
      wrapper({ children: <Text>the application</Text> }),
    );

    expect(await view.findByText(en.setup.loadFailed)).toBeOnTheScreen();

    await fireEvent.press(view.getByTestId('preferences-retry'));

    expect(await view.findByText('the application')).toBeOnTheScreen();
  });
});

describe('saving a change', () => {
  it('shows it before the server has agreed, then takes the server’s answer', async () => {
    const held = backendHoldingPatch(() => ({ status: 200, body: WARMER }));
    const view = await loaded();

    let saving: Promise<void> | null = null;
    await act(async () => {
      saving = view.result.current.save({
        personality: { formality: 'warm', verbosity: 'brief', topics: [] },
      });
    });

    expect(view.result.current.preferences.personality.formality).toBe('warm');

    await act(async () => {
      held.release();
      await saving;
    });

    expect(view.result.current.preferences).toEqual(WARMER);
  });

  it('puts the previous value back when the server refuses, and says it refused', async () => {
    const held = backendHoldingPatch(() => ({
      status: 422,
      body: { error: 'invalid_request', message: 'no' },
    }));
    const view = await loaded();

    let saving: Promise<void> | null = null;
    await act(async () => {
      saving = view.result.current.save({
        personality: { formality: 'warm', verbosity: 'brief', topics: [] },
      });
    });

    expect(view.result.current.preferences.personality.formality).toBe('warm');

    await act(async () => {
      held.release();
      await expect(saving).rejects.toThrow();
    });

    expect(view.result.current.preferences).toEqual(DEFAULT_PREFERENCES);
  });

  it('keeps a later change that was saved when an earlier one is refused', async () => {
    let refuseFirst: (() => void) | null = null;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (init?.method === 'PATCH') {
        const changes = JSON.parse(String(init.body)) as Partial<Preferences>;
        if (changes.hours !== undefined) {
          await new Promise<void>(resolve => {
            refuseFirst = resolve;
          });
          return jsonResponse(422, { error: 'invalid_request', message: 'no' });
        }
        return jsonResponse(200, { ...DEFAULT_PREFERENCES, ...changes });
      }
      const bodies: Record<string, unknown> = {
        '/v1/me': PROFILE,
        '/v1/preferences': DEFAULT_PREFERENCES,
        '/v1/onboarding': ONBOARDING_COMPLETE,
      };
      return jsonResponse(200, bodies[path]);
    }) as unknown as typeof fetch;
    const view = await loaded();

    let first: Promise<void> | null = null;
    await act(async () => {
      first = view.result.current.save({
        hours: { active: { start: '07:00', end: '22:00', zone: 'UTC' } },
      });
    });
    await act(async () => {
      await view.result.current.save({
        privacy: { transcript_retention_days: 30 },
      });
    });
    await act(async () => {
      refuseFirst?.();
      await expect(first).rejects.toThrow();
    });

    expect(view.result.current.preferences.hours.active).toBeNull();
    expect(
      view.result.current.preferences.privacy.transcript_retention_days,
    ).toBe(30);
  });

  it('stays busy while any change made in a row is still saving', async () => {
    let finishFirst: (() => void) | null = null;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (init?.method === 'PATCH') {
        const changes = JSON.parse(String(init.body)) as Partial<Preferences>;
        if (changes.hours !== undefined) {
          await new Promise<void>(resolve => {
            finishFirst = resolve;
          });
        }
        return jsonResponse(200, { ...DEFAULT_PREFERENCES, ...changes });
      }
      const bodies: Record<string, unknown> = {
        '/v1/me': PROFILE,
        '/v1/preferences': DEFAULT_PREFERENCES,
        '/v1/onboarding': ONBOARDING_COMPLETE,
      };
      return jsonResponse(200, bodies[path]);
    }) as unknown as typeof fetch;
    const view = await renderHook(() => useImmediateSave(), { wrapper });
    await waitFor(() => {
      expect(view.result.current).toBeDefined();
    });

    await act(async () => {
      view.result.current.save({
        hours: { active: { start: '07:00', end: '22:00', zone: 'UTC' } },
      });
    });
    await act(async () => {
      view.result.current.save({ privacy: { transcript_retention_days: 30 } });
    });
    await waitFor(() => {
      expect(finishFirst).not.toBeNull();
    });

    expect(view.result.current.busy).toBe(true);
    await act(async () => {
      finishFirst?.();
    });
    await waitFor(() => {
      expect(view.result.current.busy).toBe(false);
    });
  });

  it('sends only the section that changed', async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (init?.method === 'PATCH') {
        calls.push(String(init.body));
        return jsonResponse(200, DEFAULT_PREFERENCES);
      }
      const bodies: Record<string, unknown> = {
        '/v1/me': PROFILE,
        '/v1/preferences': DEFAULT_PREFERENCES,
        '/v1/onboarding': ONBOARDING_COMPLETE,
      };
      return jsonResponse(200, bodies[path]);
    }) as unknown as typeof fetch;

    const view = await loaded();
    await act(async () => {
      await view.result.current.save({
        hours: {
          active: { start: '07:00', end: '22:00', zone: 'Europe/London' },
        },
      });
    });

    const sent = JSON.parse(calls[0]);
    expect(sent.call_handling).toBeUndefined();
    expect(sent.hours.active).toEqual({
      start: '07:00',
      end: '22:00',
      zone: 'Europe/London',
    });
  });
});

describe('recording a step', () => {
  it('takes the server’s account of what is left rather than counting here', async () => {
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (path === '/v1/onboarding' && init?.method === 'POST') {
        return jsonResponse(200, onboardingAt('hours'));
      }
      const bodies: Record<string, unknown> = {
        '/v1/me': PROFILE,
        '/v1/preferences': DEFAULT_PREFERENCES,
        '/v1/onboarding': onboardingAt('call_handling'),
      };
      return jsonResponse(200, bodies[path]);
    }) as unknown as typeof fetch;

    const view = await loaded();
    await act(async () => {
      await view.result.current.recordStep('call_handling', false);
    });

    expect(view.result.current.onboarding.next_step).toBe('hours');
  });
});

describe('using it in the wrong place', () => {
  it('says where the mistake is rather than failing somewhere unrelated', async () => {
    await expect(renderHook(() => usePreferences())).rejects.toThrow(
      'usePreferences must be used inside a PreferencesProvider',
    );
  });
});
