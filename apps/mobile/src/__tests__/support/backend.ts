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
  PreferencesUpdate,
} from '@letmehandle/api-client';

import { applyChanges } from '../../preferences/changes';

export interface Reply {
  readonly status: number;
  readonly body?: unknown;
}

export const PROFILE = {
  id: 'u1',
  phone_number: '+12025550143',
  display_name: null,
  locale: 'en',
};

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

/** A backend that remembers, for tests that walk through more than one request. */
export interface RunningBackend {
  /** Every change the app asked to save, in order. */
  readonly patches: PreferencesUpdate[];
  preferences(): Preferences;
  onboarding(): Onboarding;
  /** Refuse the next save, as the server does when it disagrees with the draft. */
  refuseNextSave(reply: Reply): void;
}

/**
 * Stand in for the real thing, keeping what it is told.
 *
 * Stateful rather than a list of canned replies, because the behaviours worth testing here are
 * about what happens across requests: a step recorded changing which question comes next, a
 * refused save leaving what was stored alone.
 */
export function runningBackend(options?: {
  readonly startAt?: OnboardingStep | null;
  readonly preferences?: Preferences;
}): RunningBackend {
  const start = options?.startAt === undefined ? null : options.startAt;
  const settled = new Set<OnboardingStep>(
    start === null ? ORDER : ORDER.slice(0, ORDER.indexOf(start)),
  );
  let stored = options?.preferences ?? DEFAULT_PREFERENCES;
  let refusal: Reply | null = null;
  const patches: PreferencesUpdate[] = [];

  const progress = (): Onboarding => {
    const remaining = ORDER.filter(step => !settled.has(step));
    return {
      completed: ORDER.filter(step => settled.has(step)),
      skipped: [],
      remaining,
      next_step: remaining[0] ?? null,
      is_complete: remaining.length === 0,
    };
  };

  const answer = (status: number, payload: unknown): Response =>
    ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => payload,
    } as Response);

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    const method = init?.method ?? 'GET';
    const body: unknown =
      typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;

    if (path === '/v1/me') {
      return answer(200, PROFILE);
    }
    if (path === '/v1/preferences' && method === 'GET') {
      return answer(200, stored);
    }
    if (path === '/v1/preferences' && method === 'PATCH') {
      const changes = body as PreferencesUpdate;
      patches.push(changes);
      if (refusal !== null) {
        const reply = refusal;
        refusal = null;
        return answer(reply.status, reply.body ?? {});
      }
      stored = applyChanges(stored, changes);
      return answer(200, stored);
    }
    if (path === '/v1/onboarding' && method === 'GET') {
      return answer(200, progress());
    }
    if (path === '/v1/onboarding' && method === 'POST') {
      const update = body as { step: OnboardingStep; skipped: boolean };
      if (update.step === 'call_handling' && update.skipped) {
        // What the real backend does. There is no safe default for an unknown caller, so the
        // step cannot be passed over, and a client that offered the button would get this.
        return answer(422, { error: 'invalid_request', message: 'no' });
      }
      settled.add(update.step);
      return answer(200, progress());
    }

    // Loudly, because a request nobody wrote a handler for is either a screen doing something
    // unexpected or a test that has drifted from it, and both are worth failing over.
    throw new Error(`no handler for ${method} ${path}`);
  }) as unknown as typeof fetch;

  return {
    patches,
    preferences: () => stored,
    onboarding: progress,
    refuseNextSave: reply => {
      refusal = reply;
    },
  };
}
