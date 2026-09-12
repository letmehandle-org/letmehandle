/**
 * A backend to test against.
 *
 * Every test that renders the signed-in application needs preferences and onboarding answered,
 * because the tree will not render without them. Written once here so that a test about signing
 * out does not carry a preferences fixture it never looks at.
 *
 * Routed by method and path rather than queued in order: four endpoints answered by position is
 * a queue that has to be recounted every time a screen gains a request, and a test that fails
 * because it miscounted says nothing about the behaviour it was written for.
 */
import type {
  Onboarding,
  OnboardingStep,
  Preferences,
} from '@letmehandle/api-client';

export interface Reply {
  readonly status: number;
  readonly body?: unknown;
}

/** What the backend returns for somebody who has chosen nothing. */
export const DEFAULT_PREFERENCES: Preferences = {
  version: 1,
  locale: 'en',
  call_handling: {
    default_posture: 'handle_with_agent',
    anonymous_posture: 'handle_with_agent',
    posture_by_category: {},
    blocked_categories: [],
    escalate_at_or_above: 40,
  },
  important_contacts: [],
  hours: { working: null, quiet: null },
  authority: { capabilities: [] },
  notifications: {
    on_handled_call: false,
    on_blocked_call: false,
    on_missed_escalation: true,
    daily_summary: false,
    respect_quiet_hours: true,
  },
  personality: { formality: 'neutral', verbosity: 'normal', topics: [] },
};

const ORDER: readonly OnboardingStep[] = [
  'introduction',
  'call_handling',
  'important_contacts',
  'hours',
  'authority',
  'notifications',
  'personality',
];

/** Progress for somebody whose next question is `step`, with everything before it answered. */
export function onboardingAt(step: OnboardingStep): Onboarding {
  const at = ORDER.indexOf(step);
  return {
    completed: ORDER.slice(0, at),
    skipped: [],
    remaining: ORDER.slice(at),
    next_step: step,
    is_complete: false,
  };
}

/** Progress for somebody who has finished setting up. */
export const ONBOARDING_COMPLETE: Onboarding = {
  completed: [...ORDER],
  skipped: [],
  remaining: [],
  next_step: null,
  is_complete: true,
};

export type Handler = (body: unknown) => Reply;

/**
 * Answer requests by `METHOD /path`, and fail loudly on anything else.
 *
 * Loudly, because a request nobody wrote a handler for is either a screen doing something
 * unexpected or a test that has drifted from it, and both are worth failing over. Answering it
 * with an empty 200 would hide the first and make the second pass.
 */
export function backend(handlers: Record<string, Handler>): jest.Mock {
  const fake = jest.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    const handler = handlers[`${method} ${path}`];

    if (handler === undefined) {
      throw new Error(`no handler for ${method} ${path}`);
    }

    const reply = handler(
      typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    );
    return {
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: async () => reply.body ?? {},
    } as Response;
  });

  globalThis.fetch = fake as unknown as typeof fetch;
  return fake;
}

/** The handlers every signed-in test needs, whatever it is actually about. */
export function signedInHandlers(
  profile: unknown,
  onboarding: Onboarding = ONBOARDING_COMPLETE,
): Record<string, Handler> {
  return {
    'GET /v1/me': () => ({ status: 200, body: profile }),
    'GET /v1/preferences': () => ({ status: 200, body: DEFAULT_PREFERENCES }),
    'GET /v1/onboarding': () => ({ status: 200, body: onboarding }),
  };
}
