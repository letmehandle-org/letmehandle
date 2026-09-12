/**
 * Signing in from the app's point of view.
 *
 * The whole tree, with `fetch` standing in for the backend — so the session provider, the
 * client, the navigator and the screens are exercised together, which is where the mistakes in
 * this kind of code actually are.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { DEFAULT_PREFERENCES, ONBOARDING_COMPLETE } from './support/backend';

const NUMBER = '+12025550143';

interface Reply {
  readonly status: number;
  readonly body?: unknown;
}

function replyWith(replies: Reply[]): jest.Mock {
  const queue = [...replies];
  const fake = jest.fn(async (url: string) => {
    // Answered from the defaults rather than from the queue. The signed-in tree reads
    // preferences and onboarding before it renders, and counting those into every queue would
    // make each of these tests fail whenever a screen gains a request.
    const standing = SETUP[url.replace(/^https?:\/\/[^/]+/, '')];
    const reply = standing ?? queue.shift() ?? { status: 200, body: {} };
    return {
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: async () => reply.body ?? {},
    } as Response;
  });
  globalThis.fetch = fake as unknown as typeof fetch;
  return fake;
}

const SETUP: Record<string, Reply | undefined> = {
  '/v1/preferences': { status: 200, body: DEFAULT_PREFERENCES },
  '/v1/onboarding': { status: 200, body: ONBOARDING_COMPLETE },
};

const TOKENS = {
  access_token: 'an-access-token',
  refresh_token: 'a-refresh-token',
  token_type: 'Bearer',
  expires_in_seconds: 900,
};

const PROFILE = {
  id: 'u1',
  phone_number: NUMBER,
  display_name: null,
  locale: 'en',
};

beforeEach(() => {
  jest.clearAllMocks();
});

async function startAtPhoneEntry() {
  const view = await render(<App />);
  await waitFor(() => {
    expect(view.getByTestId('welcome-screen')).toBeOnTheScreen();
  });

  await fireEvent.press(view.getByTestId('start-button'));
  await waitFor(() => {
    expect(view.getByTestId('phone-screen')).toBeOnTheScreen();
  });
  return view;
}

describe('signing in', () => {
  it('goes from a number to a code to the application', async () => {
    replyWith([
      {
        status: 202,
        body: { challenge_id: 'challenge-1', expires_in_seconds: 300 },
      },
      { status: 200, body: TOKENS },
      { status: 200, body: PROFILE },
    ]);

    const view = await startAtPhoneEntry();

    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));

    await waitFor(() => {
      expect(view.getByTestId('code-screen')).toBeOnTheScreen();
    });
    // A development build tells the tester the fixed code the mock provider accepts.
    expect(view.getByTestId('code-testing-hint')).toHaveTextContent(
      en.code.testingHint,
    );

    await fireEvent.changeText(view.getByTestId('code-input'), '000000');
    await fireEvent.press(view.getByTestId('code-continue'));

    // The navigator follows the session rather than being told where to go, so arriving here
    // proves the session was actually established.
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
  });

  it('says so when the number is refused', async () => {
    replyWith([
      { status: 422, body: { error: 'invalid_request', message: 'no' } },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), '12345');
    await fireEvent.press(view.getByTestId('phone-continue'));

    await waitFor(() => {
      expect(view.getByText(en.phone.invalid)).toBeOnTheScreen();
    });
  });

  it('says so when there have been too many attempts', async () => {
    replyWith([
      { status: 429, body: { error: 'rate_limited', message: 'no' } },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));

    await waitFor(() => {
      expect(view.getByText(en.phone.rateLimited)).toBeOnTheScreen();
    });
  });

  it('says so when the service cannot be reached', async () => {
    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));

    await waitFor(() => {
      expect(view.getByText(en.common.noConnection)).toBeOnTheScreen();
    });
  });

  it('refuses a wrong code and clears the field', async () => {
    replyWith([
      {
        status: 202,
        body: { challenge_id: 'challenge-1', expires_in_seconds: 300 },
      },
      { status: 401, body: { error: 'invalid_credentials', message: 'no' } },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));
    await waitFor(() => {
      expect(view.getByTestId('code-screen')).toBeOnTheScreen();
    });

    await fireEvent.changeText(view.getByTestId('code-input'), '111111');
    await fireEvent.press(view.getByTestId('code-continue'));

    await waitFor(() => {
      expect(view.getByText(en.code.invalid)).toBeOnTheScreen();
    });
    // Cleared, so the next attempt starts from an empty field rather than from a code that has
    // already been refused.
    expect(view.getByTestId('code-input').props.value).toBe('');
  });

  it('falls back to a general message for a failure it does not recognise', async () => {
    // A 500 is not something to explain to somebody signing in, and guessing at a reason would
    // be worse than saying plainly that it did not work.
    replyWith([
      { status: 500, body: { error: 'internal_error', message: 'no' } },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));

    await waitFor(() => {
      expect(view.getByText(en.common.somethingWentWrong)).toBeOnTheScreen();
    });
  });

  it('falls back to a general message when the code screen fails unexpectedly', async () => {
    replyWith([
      {
        status: 202,
        body: { challenge_id: 'challenge-1', expires_in_seconds: 300 },
      },
      { status: 500, body: { error: 'internal_error', message: 'no' } },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));
    await waitFor(() => {
      expect(view.getByTestId('code-screen')).toBeOnTheScreen();
    });

    await fireEvent.changeText(view.getByTestId('code-input'), '000000');
    await fireEvent.press(view.getByTestId('code-continue'));

    await waitFor(() => {
      expect(view.getByText(en.common.somethingWentWrong)).toBeOnTheScreen();
    });
  });

  it('says so when the code screen cannot reach the service', async () => {
    replyWith([
      {
        status: 202,
        body: { challenge_id: 'challenge-1', expires_in_seconds: 300 },
      },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));
    await waitFor(() => {
      expect(view.getByTestId('code-screen')).toBeOnTheScreen();
    });

    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    await fireEvent.changeText(view.getByTestId('code-input'), '000000');
    await fireEvent.press(view.getByTestId('code-continue'));

    await waitFor(() => {
      expect(view.getByText(en.common.noConnection)).toBeOnTheScreen();
    });
  });

  it('can go back from the code screen to change the number', async () => {
    replyWith([
      {
        status: 202,
        body: { challenge_id: 'challenge-1', expires_in_seconds: 300 },
      },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));
    await waitFor(() => {
      expect(view.getByTestId('code-screen')).toBeOnTheScreen();
    });

    await fireEvent.press(view.getByTestId('code-screen-back'));

    await waitFor(() => {
      expect(view.getByTestId('phone-screen')).toBeOnTheScreen();
    });
  });
});
