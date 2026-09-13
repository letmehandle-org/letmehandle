/** Signing in through the whole tree, with `fetch` standing in for the backend. */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { DEFAULT_PREFERENCES, ONBOARDING_COMPLETE } from './support/backend';
import { jsonResponse } from './support/http';

const NUMBER = '+12025550143';

interface Reply {
  readonly status: number;
  readonly body?: unknown;
  readonly headers?: Record<string, string>;
}

function replyWith(replies: Reply[]): jest.Mock {
  const queue = [...replies];
  const fake = jest.fn(async (url: string, _init?: RequestInit) => {
    // Preferences and onboarding answer from fixed bodies, outside the queue.
    const standing = SETUP[url.replace(/^https?:\/\/[^/]+/, '')];
    const reply = standing ?? queue.shift() ?? { status: 200, body: {} };
    return jsonResponse(reply.status, reply.body ?? {}, reply.headers ?? {});
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
        body: {
          challenge_id: 'challenge-1',
          expires_in_seconds: 300,
          resend_after_seconds: 0,
        },
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

    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
  });

  it('enters the application when the profile cannot be read straight after the code', async () => {
    replyWith([
      {
        status: 202,
        body: {
          challenge_id: 'challenge-1',
          expires_in_seconds: 300,
          resend_after_seconds: 30,
        },
      },
      { status: 200, body: TOKENS },
      { status: 503, body: { error: 'database_unavailable', message: 'down' } },
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
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
  });

  it('says so when the server refuses a number that looked whole', async () => {
    replyWith([
      { status: 422, body: { error: 'invalid_request', message: 'no' } },
    ]);

    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
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
        body: {
          challenge_id: 'challenge-1',
          expires_in_seconds: 300,
          resend_after_seconds: 0,
        },
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
    expect(view.getByTestId('code-input').props.value).toBe('');
  });

  it('falls back to a general message for a failure it does not recognise', async () => {
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
        body: {
          challenge_id: 'challenge-1',
          expires_in_seconds: 300,
          resend_after_seconds: 0,
        },
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
        body: {
          challenge_id: 'challenge-1',
          expires_in_seconds: 300,
          resend_after_seconds: 0,
        },
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
        body: {
          challenge_id: 'challenge-1',
          expires_in_seconds: 300,
          resend_after_seconds: 0,
        },
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

describe('asking for another code', () => {
  const SENT = {
    status: 202,
    body: {
      challenge_id: 'challenge-1',
      expires_in_seconds: 300,
      resend_after_seconds: 30,
    },
  };

  async function atTheCodeScreen(replies: Reply[]) {
    const fetched = replyWith([SENT, ...replies]);
    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));
    await waitFor(() => {
      expect(view.getByTestId('code-screen')).toBeOnTheScreen();
    });
    return { fetched, view };
  }

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('counts down to when another code may be sent, then sends one that is the one used', async () => {
    const { fetched, view } = await atTheCodeScreen([
      {
        status: 202,
        body: {
          challenge_id: 'challenge-2',
          expires_in_seconds: 300,
          resend_after_seconds: 60,
        },
      },
      { status: 200, body: TOKENS },
      { status: 200, body: PROFILE },
    ]);
    expect(view.getByTestId('code-resend')).toBeDisabled();
    expect(view.getByTestId('code-resend')).toHaveTextContent(/in 0:(30|29)/);

    // Thirty-one seconds later, as the screen's clock sees it; its own tick notices within one.
    const later = Date.now() + 31_000;
    jest.spyOn(Date, 'now').mockImplementation(() => later);
    await waitFor(
      () => {
        expect(view.getByTestId('code-resend')).toBeEnabled();
      },
      { timeout: 2_500 },
    );
    await fireEvent.press(view.getByTestId('code-resend'));
    expect(await view.findByTestId('code-resent')).toHaveTextContent(
      en.code.resent,
    );
    expect(view.getByTestId('code-resend')).toBeDisabled();

    await fireEvent.changeText(view.getByTestId('code-input'), '000000');
    await fireEvent.press(view.getByTestId('code-continue'));
    await waitFor(() => {
      expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    });
    const verify = fetched.mock.calls.find(([url]) =>
      String(url).endsWith('/v1/auth/verify'),
    );
    expect(JSON.parse(String(verify?.[1]?.body)).challenge_id).toBe(
      'challenge-2',
    );
  });

  it('says how long to wait when the number has had too many codes', async () => {
    replyWith([
      {
        status: 429,
        body: { error: 'rate_limited', message: 'no' },
        headers: { 'Retry-After': '120' },
      },
    ]);
    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));

    expect(
      await view.findByText(
        'Too many codes for this number. Try again in 2 minutes.',
      ),
    ).toBeOnTheScreen();
  });

  it('says a number that has had too many wrong codes must wait', async () => {
    const { view } = await atTheCodeScreen([
      {
        status: 429,
        body: { error: 'rate_limited', message: 'no' },
        headers: { 'Retry-After': '86400' },
      },
    ]);
    await fireEvent.changeText(view.getByTestId('code-input'), '000000');
    await fireEvent.press(view.getByTestId('code-continue'));

    expect(await view.findByTestId('code-problem')).toHaveTextContent(
      'Too many wrong codes. Try again in 24 hours.',
    );
  });

  it('keeps counting down when a resend is refused', async () => {
    const { view } = await atTheCodeScreen([
      {
        status: 429,
        body: { error: 'rate_limited', message: 'no' },
        headers: { 'Retry-After': '45' },
      },
    ]);
    const later = Date.now() + 31_000;
    jest.spyOn(Date, 'now').mockImplementation(() => later);
    await waitFor(
      () => {
        expect(view.getByTestId('code-resend')).toBeEnabled();
      },
      { timeout: 2_500 },
    );

    await fireEvent.press(view.getByTestId('code-resend'));

    expect(await view.findByTestId('code-problem')).toHaveTextContent(
      'Too many codes for this number. Try again in 45 seconds.',
    );
    expect(view.getByTestId('code-resend')).toBeDisabled();
  });

  it('says the country is not served rather than that the number is wrong', async () => {
    replyWith([
      {
        status: 422,
        body: { error: 'unserved_country', message: 'no' },
      },
    ]);
    const view = await startAtPhoneEntry();
    await fireEvent.changeText(view.getByTestId('phone-input'), NUMBER);
    await fireEvent.press(view.getByTestId('phone-continue'));

    expect(await view.findByText(en.phone.unserved)).toBeOnTheScreen();
  });
});
