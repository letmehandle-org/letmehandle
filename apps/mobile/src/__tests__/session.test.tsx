/** Restoring a session, keeping it through outages, and ending it. */
import {
  act,
  fireEvent,
  render,
  renderHook,
  waitFor,
} from '@testing-library/react-native';
import React from 'react';
import { AppState } from 'react-native';

import { App } from '../App';
import { SessionProvider, useSession } from '../auth/SessionProvider';
import * as tokenStore from '../auth/tokenStore';
import { DEFAULT_PREFERENCES, ONBOARDING_COMPLETE } from './support/backend';
import { jsonResponse } from './support/http';

const NUMBER = '+12025550143';
const PROFILE = {
  id: 'u1',
  phone_number: NUMBER,
  display_name: null,
  locale: 'en',
};
const TOKENS = {
  access_token: 'renewed-access-token',
  refresh_token: 'renewed-refresh-token',
  token_type: 'Bearer',
  expires_in_seconds: 900,
};

jest.mock('../auth/tokenStore', () => ({
  ...jest.requireActual('../auth/tokenStore'),
  loadSession: jest.fn(),
  saveSession: jest.fn(),
  clearSession: jest.fn(),
}));

const store = tokenStore as jest.Mocked<typeof tokenStore>;

interface Reply {
  readonly status: number;
  readonly body?: unknown;
}

function replyWith(replies: Reply[]): jest.Mock {
  const queue = [...replies];
  const fake = jest.fn(async (url: string) => {
    // Preferences and onboarding answer from fixed bodies, outside the queue.
    const standing = SETUP[url.replace(/^https?:\/\/[^/]+/, '')];
    const reply = standing ?? queue.shift() ?? { status: 200, body: {} };
    return jsonResponse(reply.status, reply.body ?? {});
  });
  globalThis.fetch = fake as unknown as typeof fetch;
  return fake;
}

const SETUP_BODIES: Record<string, unknown> = {
  '/v1/preferences': DEFAULT_PREFERENCES,
  '/v1/onboarding': ONBOARDING_COMPLETE,
};

const SETUP: Record<string, Reply | undefined> = {
  '/v1/preferences': { status: 200, body: DEFAULT_PREFERENCES },
  '/v1/onboarding': { status: 200, body: ONBOARDING_COMPLETE },
};

beforeEach(() => {
  jest.clearAllMocks();
  store.loadSession.mockResolvedValue(null);
  store.saveSession.mockResolvedValue(undefined);
  store.clearSession.mockResolvedValue(undefined);
});

describe('starting up', () => {
  it('opens the application when a stored session is still good', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    replyWith([{ status: 200, body: PROFILE }]);

    const view = await render(<App />);

    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
  });

  it('renews before the first request when the token is nearly expired', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'nearly-expired',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 1_000,
    });
    const fetched = replyWith([
      { status: 200, body: TOKENS },
      { status: 200, body: PROFILE },
    ]);

    const view = await render(<App />);

    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    expect(fetched.mock.calls[0]?.[0]).toContain('/v1/auth/refresh');
    expect(store.saveSession).toHaveBeenCalled();
  });

  it('signs out when the stored session can no longer be renewed', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'expired',
      refreshToken: 'revoked',
      accessTokenExpiresAt: Date.now() - 1_000,
    });
    replyWith([
      { status: 401, body: { error: 'invalid_credentials', message: 'no' } },
    ]);

    const view = await render(<App />);

    await waitFor(() => {
      expect(view.getByTestId('welcome-screen')).toBeOnTheScreen();
    });
    expect(store.clearSession).toHaveBeenCalled();
  });

  it('signs out when the stored session is rejected outright', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'revoked',
      refreshToken: 'revoked',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    replyWith([
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
      { status: 401, body: { error: 'invalid_credentials', message: 'no' } },
    ]);

    const view = await render(<App />);

    await waitFor(() => {
      expect(view.getByTestId('welcome-screen')).toBeOnTheScreen();
    });
  });

  it('shows the sign-in screens when there is no stored session', async () => {
    replyWith([]);
    const view = await render(<App />);

    await waitFor(() => {
      expect(view.getByTestId('welcome-screen')).toBeOnTheScreen();
    });
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe('never being asked for the number again without cause', () => {
  const offline = (): void => {
    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;
  };

  it('opens signed in with no signal, even when the token needed renewing', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'nearly-expired',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 1_000,
    });
    offline();

    const view = await render(<App />);

    await waitFor(() => {
      expect(view.getByTestId('preferences-unavailable')).toBeOnTheScreen();
    });
    expect(view.queryByTestId('welcome-screen')).toBeNull();
    expect(store.clearSession).not.toHaveBeenCalled();
  });

  it('stays signed in when the server is down while renewing', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'expired',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() - 1_000,
    });
    replyWith([
      { status: 503, body: { error: 'database_unavailable', message: 'down' } },
    ]);

    const view = await render(<App />);

    await waitFor(() => {
      expect(view.queryByTestId('restoring')).toBeNull();
    });
    expect(view.queryByTestId('welcome-screen')).toBeNull();
    expect(store.clearSession).not.toHaveBeenCalled();
  });

  it('stays signed in when the profile cannot be fetched, and fetches it once back in front', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    offline();
    const listeners: ((state: string) => void)[] = [];
    jest.spyOn(AppState, 'addEventListener').mockImplementation(((
      _type: string,
      listener: (state: string) => void,
    ) => {
      listeners.push(listener);
      return { remove: () => undefined };
    }) as unknown as typeof AppState.addEventListener);
    const { result } = await renderHook(() => useSession(), {
      wrapper: ({ children }) => <SessionProvider>{children}</SessionProvider>,
    });
    await waitFor(() => {
      expect(result.current.status).toBe('signed-in');
    });
    expect(result.current.profile).toBeNull();

    replyWith([{ status: 200, body: PROFILE }]);
    for (const listener of listeners) {
      listener('active');
    }
    await waitFor(() => {
      expect(result.current.profile).toEqual(PROFILE);
    });
  });

  it('keeps the session when a request cannot renew for want of a signal', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    replyWith([{ status: 200, body: PROFILE }]);
    const { result } = await renderHook(() => useSession(), {
      wrapper: ({ children }) => <SessionProvider>{children}</SessionProvider>,
    });
    await waitFor(() => {
      expect(result.current.status).toBe('signed-in');
    });

    // The access token is refused, and renewing it finds no network.
    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      if (calls === 1) {
        return jsonResponse(401, { error: 'not_authenticated', message: 'no' });
      }
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    await expect(result.current.api.me()).rejects.toThrow();
    expect(result.current.status).toBe('signed-in');
    expect(store.clearSession).not.toHaveBeenCalled();
  });
});

describe('signing out', () => {
  it('tells the backend, forgets the session and returns to the sign-in screens', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    const fetched = replyWith([
      { status: 200, body: PROFILE },
      { status: 204 },
    ]);

    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });

    await fireEvent.press(view.getByTestId('tab-settings'));
    await fireEvent.press(view.getByTestId('settings-open-account'));
    await waitFor(() => {
      expect(view.getByTestId('profile-screen')).toBeOnTheScreen();
    });

    await fireEvent.press(view.getByTestId('profile-sign-out'));

    await waitFor(() => {
      expect(view.getByTestId('welcome-screen')).toBeOnTheScreen();
    });
    // The refresh token is revoked at the backend as well as forgotten here.
    expect(fetched.mock.calls.at(-1)?.[0]).toContain('/v1/auth/signout');
    expect(store.clearSession).toHaveBeenCalled();
  });

  it('still signs out locally when the backend cannot be told', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    // The signed-in tree answers by path; the sign-out request fails.
    globalThis.fetch = (async (url: string) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      const standing = { '/v1/me': PROFILE, ...SETUP_BODIES }[path];
      if (standing === undefined) {
        throw new TypeError('Network request failed');
      }
      return jsonResponse(200, standing);
    }) as unknown as typeof fetch;

    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });

    await fireEvent.press(view.getByTestId('tab-settings'));
    await fireEvent.press(view.getByTestId('settings-open-account'));
    await waitFor(() => {
      expect(view.getByTestId('profile-screen')).toBeOnTheScreen();
    });
    await fireEvent.press(view.getByTestId('profile-sign-out'));

    await waitFor(() => {
      expect(view.getByTestId('welcome-screen')).toBeOnTheScreen();
    });
  });
});

describe('signing out while a request is under way', () => {
  it('forgets the session on this phone before the backend has answered', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    globalThis.fetch = (async (url: string) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (path === '/v1/auth/signout') {
        return new Promise<Response>(() => undefined);
      }
      return jsonResponse(200, { '/v1/me': PROFILE, ...SETUP_BODIES }[path]);
    }) as unknown as typeof fetch;
    const { result } = await renderHook(() => useSession(), {
      wrapper: ({ children }) => <SessionProvider>{children}</SessionProvider>,
    });
    await waitFor(() => {
      expect(result.current.status).toBe('signed-in');
    });

    result.current.signOut().catch(() => undefined);

    await waitFor(() => {
      expect(result.current.status).toBe('signed-out');
    });
    expect(store.clearSession).toHaveBeenCalled();
  });

  it('does not keep a renewal that finishes after signing out', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    let finishRenewal: (() => void) | null = null;
    let profileReads = 0;
    globalThis.fetch = (async (url: string) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '');
      if (path === '/v1/me') {
        profileReads += 1;
        return profileReads === 1
          ? jsonResponse(200, PROFILE)
          : jsonResponse(401, { error: 'not_authenticated', message: 'no' });
      }
      if (path === '/v1/auth/refresh') {
        await new Promise<void>(resolve => {
          finishRenewal = resolve;
        });
        return jsonResponse(200, TOKENS);
      }
      return jsonResponse(204);
    }) as unknown as typeof fetch;
    const { result } = await renderHook(() => useSession(), {
      wrapper: ({ children }) => <SessionProvider>{children}</SessionProvider>,
    });
    await waitFor(() => {
      expect(result.current.status).toBe('signed-in');
    });

    const request = result.current.api.me().catch(() => undefined);
    await waitFor(() => {
      expect(finishRenewal).not.toBeNull();
    });
    await act(async () => {
      await result.current.signOut();
    });
    await act(async () => {
      finishRenewal?.();
      await request;
    });

    expect(result.current.status).toBe('signed-out');
    expect(store.saveSession).not.toHaveBeenCalled();
  });
});

describe('the profile', () => {
  it('shows the number and saves a name', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    replyWith([
      { status: 200, body: PROFILE },
      { status: 200, body: { ...PROFILE, display_name: 'Alex' } },
      { status: 200, body: { ...PROFILE, display_name: 'Alex' } },
    ]);

    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    await fireEvent.press(view.getByTestId('tab-settings'));
    await fireEvent.press(view.getByTestId('settings-open-account'));
    await waitFor(() => {
      expect(view.getByTestId('profile-screen')).toBeOnTheScreen();
    });

    expect(view.getByTestId('profile-number').props.children).toBe(NUMBER);

    await fireEvent.changeText(view.getByTestId('profile-name-input'), 'Alex');
    await fireEvent.press(view.getByTestId('profile-save'));

    await waitFor(() => {
      expect(view.getByTestId('profile-saved')).toBeOnTheScreen();
    });
  });

  it('says so when saving fails', async () => {
    store.loadSession.mockResolvedValue({
      accessToken: 'a-token',
      refreshToken: 'a-refresh-token',
      accessTokenExpiresAt: Date.now() + 600_000,
    });
    replyWith([
      { status: 200, body: PROFILE },
      { status: 500, body: { error: 'internal_error', message: 'no' } },
    ]);

    const view = await render(<App />);
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    await fireEvent.press(view.getByTestId('tab-settings'));
    await fireEvent.press(view.getByTestId('settings-open-account'));
    await waitFor(() => {
      expect(view.getByTestId('profile-screen')).toBeOnTheScreen();
    });

    await fireEvent.changeText(view.getByTestId('profile-name-input'), 'Alex');
    await fireEvent.press(view.getByTestId('profile-save'));

    await waitFor(() => {
      expect(view.getByTestId('profile-name-input')).toBeOnTheScreen();
    });
  });
});

describe('using the session outside a provider', () => {
  it('says where the mistake is', async () => {
    const errors = jest
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);

    await expect(renderHook(() => useSession())).rejects.toThrow(
      'SessionProvider',
    );

    errors.mockRestore();
  });
});
