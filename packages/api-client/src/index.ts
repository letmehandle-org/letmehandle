/**
 * The backend's wire types, named.
 *
 * `schema.ts` is generated and unreadable by design. These aliases are the readable surface:
 * they name the handful of shapes the app actually uses, so a screen imports `TokenPair`
 * rather than reaching into a generated path that changes shape whenever the generator does.
 */
import type { components, paths } from './schema';

export type { components, paths };

type Schemas = components['schemas'];

export type ChallengeRequest = Schemas['ChallengeRequest'];
export type ChallengeResponse = Schemas['ChallengeResponse'];
export type VerifyRequest = Schemas['VerifyRequest'];
export type RefreshRequest = Schemas['RefreshRequest'];
export type SignOutRequest = Schemas['SignOutRequest'];
export type TokenPair = Schemas['TokenResponse'];
export type Profile = Schemas['ProfileResponse'];
export type UpdateProfileRequest = Schemas['UpdateProfileRequest'];

export type Preferences = Schemas['PreferencesResponse'];
export type PreferencesUpdate = Schemas['PreferencesUpdate'];
export type CallHandling = Schemas['CallHandlingPayload'];
export type Hours = Schemas['HoursPayload'];
export type TimeWindow = Schemas['TimeWindowPayload'];
export type ImportantContact = Schemas['ImportantContactPayload'];
export type Authority = Schemas['AuthorityPayload'];
export type Notifications = Schemas['NotificationsPayload'];
export type Personality = Schemas['PersonalityPayload'];
export type Onboarding = Schemas['OnboardingResponse'];
export type OnboardingUpdate = Schemas['OnboardingUpdate'];
export type OnboardingStep = Schemas['OnboardingStep'];
export type HandlingPosture = Schemas['HandlingPosture'];
export type CallerCategory = Schemas['CallerCategory'];
export type Capability = Schemas['Capability'];
export type Formality = Schemas['Formality'];
export type Verbosity = Schemas['Verbosity'];
export type CallImportance = Schemas['CallImportance'];

export type CallReport = Schemas['CallReportPayload'];
export type CallReportBatch = Schemas['CallReportBatch'];
export type CallReportReceipt = Schemas['CallReportReceipt'];
export type ReportedCallKind = Schemas['ReportedCallKind'];
export type ScreeningDecision = Schemas['ScreeningDecision'];
export type CallEnding = Schemas['CallEnding'];

export type CallPage = Schemas['CallPageResponse'];
export type CallSummary = Schemas['CallListItem'];
export type CallDetail = Schemas['CallDetailResponse'];
export type CallOutcome = Schemas['CallOutcome'];
export type CallIntent = Schemas['CallIntent'];
export type CallStatus = Schemas['CallStatus'];
export type CallRouting = Schemas['CallHandling'];
export type Caller = Schemas['CallerPayload'];
export type ExtractedDetail = Schemas['ExtractedDetailPayload'];
export type Transcript = Schemas['TranscriptResponse'];
export type TranscriptLine = Schemas['TranscriptLinePayload'];
export type Speaker = Schemas['Speaker'];
export type Escalation = Schemas['EscalationContextResponse'];
export type EscalationReason = Schemas['EscalationReason'];
export type Privacy = Schemas['PrivacyPayload'];

/**
 * The machine-readable codes the API returns with a failure.
 *
 * A client branches on these. The message beside them is for a person and will be rewritten,
 * so anything that parses one has turned prose into an interface.
 */
export const ERROR_CODES = {
  invalidRequest: 'invalid_request',
  invalidCredentials: 'invalid_credentials',
  notAuthenticated: 'not_authenticated',
  rateLimited: 'rate_limited',
  unservedCountry: 'unserved_country',
  transcriptPurged: 'transcript_purged',
  transcriptNotRecorded: 'transcript_not_recorded',
  databaseUnavailable: 'database_unavailable',
  internal: 'internal_error',
} as const;

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES];

/** The one error shape every endpoint returns. */
export interface ApiErrorBody {
  error: ErrorCode | string;
  message: string;
  correlation_id?: string;
  detail?: { field: string; problem: string }[];
}
