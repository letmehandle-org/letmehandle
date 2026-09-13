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
  CallDetail,
  CallReport,
  CallReportBatch,
  CallSummary,
  Escalation,
  Onboarding,
  OnboardingStep,
  Preferences,
  PreferencesUpdate,
  Transcript,
} from '@letmehandle/api-client';

import type { Voice, VoiceCapabilities } from '../../api/voice';
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
  call_forwarding: null,
};

/** The number a deployment whose calls arrive forwarded asks its users to forward to. */
export const FORWARDING_NUMBER = '+12025550100';

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
  hours: { active: null },
  authority: { capabilities: [] },
  notifications: {
    on_handled_call: false,
    on_blocked_call: false,
    on_missed_escalation: true,
    daily_summary: false,
    respect_active_hours: true,
  },
  personality: { formality: 'neutral', verbosity: 'normal', topics: [] },
  privacy: { transcript_retention_days: 7 },
};

/** What the shipped provider offers: a few voices, and nothing else it can do with them. */
export const VOICES: readonly Voice[] = [
  { id: 'ash', name: 'Ash', locales: ['en'], previewable: false },
  { id: 'briar', name: 'Briar', locales: ['en'], previewable: false },
  { id: 'cove', name: 'Cove', locales: ['en'], previewable: false },
];

export const DEFAULT_VOICE_ID = 'ash';

export const VOICE_CAPABILITIES: VoiceCapabilities = {
  builtin_voices: true,
  preview: false,
  custom_voice: false,
  cloning: false,
  local_inference: false,
  realtime_streaming: false,
};

/** How a deployment's voice provider is set up, for a test that cares. */
export interface VoiceSetup {
  readonly voices?: readonly Voice[];
  readonly capabilities?: VoiceCapabilities;
  /** The voice this user has chosen, if any. */
  readonly persona?: string | null;
  /** A cloned voice, which takes precedence over a chosen one where a provider has them. */
  readonly cloned?: string | null;
}

const ORDER: readonly OnboardingStep[] = [
  'call_handling',
  'hours',
  'authority',
  'notifications',
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
  /** The voice this user has chosen, or null when they have left it to the provider. */
  chosenVoice(): string | null;
  /**
   * Stop offering a voice, without telling the app.
   *
   * What happens when a provider withdraws one: a client holding the old catalogue still offers
   * it, and the server refuses it with a 422 when somebody picks it.
   */
  withdrawVoice(voiceId: string): void;
  /** Every call report stored, once each, in the order they arrived. */
  readonly reports: CallReport[];
  /** Fail the next report request as a server fault, as a backend that is down does. */
  failNextReport(): void;
  /** The calls still held, after any the app deleted. */
  calls(): readonly CallDetail[];
  /** Every call listing asked for, as its query string. */
  readonly listings: string[];
  /** Whether the app asked for the account to be deleted. */
  accountDeleted(): boolean;
  /** Fail the next request to this path prefix, with this reply. */
  failNext(pathPrefix: string, reply: Reply): void;
}

/** A reply that stands in for a resource: the body, or a refusal with the API's own code. */
export type Held<T> = T | { readonly status: number; readonly error: string };

export interface HistorySetup {
  readonly calls: readonly CallDetail[];
  readonly transcripts?: Readonly<Record<string, Held<Transcript>>>;
  readonly escalations?: Readonly<Record<string, Held<Escalation>>>;
  /** How many calls a page holds. */
  readonly pageSize?: number;
}

/** A finished call the assistant settled, with every field something a screen may read. */
export function aCall(changes: Partial<CallDetail> = {}): CallDetail {
  return {
    id: 'call-1',
    caller: {
      category: 'delivery',
      display_name: null,
      number_withheld: false,
    },
    status: 'ended',
    started_at: new Date().toISOString(),
    duration_seconds: 192,
    outcome: 'resolved_by_agent',
    handling: 'assistant',
    headline: 'Your parcel will be left at the gate before 6 pm.',
    intent: 'delivery_in_progress',
    importance: 30,
    human_joined: false,
    escalation_reason: null,
    details: [
      { label: 'address', value: 'Gate, with the guard', evidence: null },
    ],
    timings: {
      received_at: new Date().toISOString(),
      answered_at: new Date().toISOString(),
      escalated_at: null,
      human_joined_at: null,
      ended_at: new Date().toISOString(),
    },
    transcript_available: true,
    transcript_expires_at: '2026-09-19T10:24:00Z',
    transcript_retention_days: 7,
    ...changes,
  };
}

function summaryOf(call: CallDetail): CallSummary {
  return {
    id: call.id,
    caller: call.caller,
    status: call.status,
    started_at: call.started_at,
    duration_seconds: call.duration_seconds,
    outcome: call.outcome,
    headline: call.headline,
    human_joined: call.human_joined,
  };
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
  readonly voice?: VoiceSetup;
  /** Whether calls reach this deployment forwarded, which adds a step to setup. */
  readonly forwarded?: boolean;
  readonly history?: HistorySetup;
}): RunningBackend {
  let held = [...(options?.history?.calls ?? [])];
  const listings: string[] = [];
  const pageSize = options?.history?.pageSize ?? 20;
  let deleted = false;
  const failures: { prefix: string; reply: Reply }[] = [];
  const heldReply = (value: Held<unknown> | undefined): Response =>
    value === undefined
      ? answer(404, { error: 'not_found', message: 'no' })
      : typeof value === 'object' &&
        value !== null &&
        'error' in value &&
        'status' in value
      ? answer((value as { status: number }).status, {
          error: (value as { error: string }).error,
          message: 'no',
        })
      : answer(200, value);
  const forwarded = options?.forwarded ?? false;
  const steps: readonly OnboardingStep[] = forwarded
    ? [ORDER[0], 'call_forwarding', ...ORDER.slice(1)]
    : ORDER;
  const start = options?.startAt === undefined ? null : options.startAt;
  const settled = new Set<OnboardingStep>(
    start === null ? steps : steps.slice(0, steps.indexOf(start)),
  );
  let stored = options?.preferences ?? DEFAULT_PREFERENCES;
  let refusal: Reply | null = null;
  const patches: PreferencesUpdate[] = [];
  const reports: CallReport[] = [];
  let reportFailure = false;

  const capabilities = options?.voice?.capabilities ?? VOICE_CAPABILITIES;
  let offered = [...(options?.voice?.voices ?? VOICES)];
  const cloned = options?.voice?.cloned ?? null;
  let persona = options?.voice?.persona ?? null;

  const offers = (voiceId: string): boolean =>
    offered.some(voice => voice.id === voiceId);

  // The fallback chain from D-009: the cloned voice, then the chosen one, then the default.
  // Each step falls through when the voice is not on offer, which is what the screen is meant
  // to be able to show.
  const resolved = (): string => {
    if (cloned !== null && offers(cloned)) {
      return cloned;
    }
    if (persona !== null && offers(persona)) {
      return persona;
    }
    return DEFAULT_VOICE_ID;
  };

  const selection = (): unknown => ({
    cloned_voice_id: cloned,
    persona_voice_id: persona,
    resolved_voice_id: resolved(),
  });

  const progress = (): Onboarding => {
    const remaining = steps.filter(step => !settled.has(step));
    return {
      completed: steps.filter(step => settled.has(step)),
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

    const failure = failures.findIndex(entry => path.startsWith(entry.prefix));
    if (failure !== -1) {
      const [{ reply }] = failures.splice(failure, 1);
      return answer(reply.status, reply.body ?? {});
    }
    if (
      path.startsWith('/v1/calls?') ||
      (path === '/v1/calls' && method === 'GET')
    ) {
      const query = new URLSearchParams(path.split('?')[1] ?? '');
      listings.push(query.toString());
      const matching = held.filter(
        call =>
          (!query.has('outcome') || call.outcome === query.get('outcome')) &&
          (!query.has('human_joined') ||
            String(call.human_joined) === query.get('human_joined')),
      );
      const from = Number(query.get('cursor') ?? '0');
      const page = matching.slice(from, from + pageSize);
      return answer(200, {
        calls: page.map(summaryOf),
        next_cursor:
          from + pageSize < matching.length ? String(from + pageSize) : null,
      });
    }
    const transcript = /^\/v1\/calls\/([^/]+)\/transcript$/.exec(path);
    if (transcript !== null) {
      return heldReply(
        options?.history?.transcripts?.[decodeURIComponent(transcript[1])],
      );
    }
    const oneCall = /^\/v1\/calls\/([^/]+)$/.exec(path);
    if (oneCall !== null && path !== '/v1/calls/reports') {
      const id = decodeURIComponent(oneCall[1]);
      if (method === 'DELETE') {
        held = held.filter(call => call.id !== id);
        return answer(204, null);
      }
      const call = held.find(entry => entry.id === id);
      return call === undefined
        ? answer(404, { error: 'call_not_found', message: 'no' })
        : answer(200, call);
    }
    const escalation = /^\/v1\/escalations\/([^/]+)$/.exec(path);
    if (escalation !== null) {
      return heldReply(
        options?.history?.escalations?.[decodeURIComponent(escalation[1])],
      );
    }
    if (path === '/v1/me' && method === 'DELETE') {
      deleted = true;
      return answer(204, null);
    }
    if (path === '/v1/auth/signout') {
      return answer(204, null);
    }
    if (path === '/v1/me') {
      return answer(200, {
        ...PROFILE,
        call_forwarding: forwarded ? { number: FORWARDING_NUMBER } : null,
      });
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
    if (path === '/v1/voices' && method === 'GET') {
      return answer(200, {
        provider: 'catalogue',
        default_voice_id: DEFAULT_VOICE_ID,
        capabilities,
        voices: offered,
      });
    }
    if (path === '/v1/preferences/voice' && method === 'GET') {
      return answer(200, selection());
    }
    if (path === '/v1/preferences/voice' && method === 'PUT') {
      const chosen = (body as { persona_voice_id: string | null })
        .persona_voice_id;
      if (chosen !== null && !offers(chosen)) {
        return answer(422, {
          error: 'invalid_request',
          message: 'not on offer',
        });
      }
      persona = chosen;
      return answer(200, selection());
    }
    if (path === '/v1/calls/reports' && method === 'POST') {
      if (reportFailure) {
        reportFailure = false;
        return answer(500, { error: 'internal_error', message: 'down' });
      }
      const accepted: string[] = [];
      const duplicates: string[] = [];
      for (const report of (body as CallReportBatch).reports) {
        // Idempotent by event id, as the backend is.
        if (reports.some(existing => existing.event_id === report.event_id)) {
          duplicates.push(report.event_id);
        } else {
          reports.push(report);
          accepted.push(report.event_id);
        }
      }
      return answer(200, { accepted, duplicates, rejected: [] });
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
    chosenVoice: () => persona,
    withdrawVoice: voiceId => {
      offered = offered.filter(voice => voice.id !== voiceId);
    },
    reports,
    failNextReport: () => {
      reportFailure = true;
    },
    calls: () => held,
    listings,
    accountDeleted: () => deleted,
    failNext: (prefix, reply) => {
      failures.push({ prefix, reply });
    },
  };
}
