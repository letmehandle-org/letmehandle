/**
 * The two documents that cross into and out of the native screening service.
 *
 * The rules snapshot goes in: the deterministic part of this user's preferences, in the shape the
 * Kotlin `CallRulesSnapshotCodec` reads. Call reports come out: exactly the backend's
 * `CallReportPayload`, so they are forwarded without translation.
 *
 * `wire-examples.json` beside this file is read by these functions' tests and by the Kotlin
 * codecs' tests. A change to either side that the other would not accept fails both suites.
 */
import type {
  CallEnding,
  CallReport,
  HandlingPosture,
  Preferences,
  ReportedCallKind,
  ScreeningDecision,
} from '@letmehandle/api-client';

/** 2 dropped quiet hours: the user's hours decide when the assistant answers, not a handset (D-030). */
export const RULES_SNAPSHOT_VERSION = 2;

export interface RulesSnapshot {
  readonly version: typeof RULES_SNAPSHOT_VERSION;
  readonly synced_at: string;
  readonly default_posture: HandlingPosture;
  readonly anonymous_posture: HandlingPosture;
  readonly posture_by_category: Readonly<Record<string, HandlingPosture>>;
  readonly blocked_categories: readonly string[];
  readonly important_contacts: readonly {
    readonly phone_number: string;
    readonly posture: HandlingPosture;
  }[];
}

/**
 * The rules a handset applies before a call rings, from the preferences as they stand.
 *
 * Only the deterministic part. Contact labels stay behind: the handset needs a number and what to
 * do with it, and a copy of what the user calls somebody is a copy nothing reads. So do the user's
 * hours, which no decision on the handset reads.
 */
export function buildRulesSnapshot(
  preferences: Preferences,
  now: Date,
): RulesSnapshot {
  const handling = preferences.call_handling;
  return {
    version: RULES_SNAPSHOT_VERSION,
    synced_at: now.toISOString(),
    default_posture: handling.default_posture,
    anonymous_posture: handling.anonymous_posture,
    posture_by_category: { ...(handling.posture_by_category ?? {}) },
    blocked_categories: [...(handling.blocked_categories ?? [])],
    important_contacts: preferences.important_contacts.map(contact => ({
      phone_number: contact.phone_number,
      posture: contact.posture,
    })),
  };
}

const KINDS: readonly ReportedCallKind[] = ['incoming', 'answered', 'ended'];
const DECISIONS: readonly ScreeningDecision[] = ['allow', 'reject', 'silence'];
const ENDINGS: readonly CallEnding[] = ['screened_out', 'missed', 'completed'];

/** Why the handset's call reports, or one of them, could not be read. Names a field, never a value. */
export class MalformedCallReports extends Error {
  constructor(reason: string) {
    super(`the handset's call reports could not be read: ${reason}`);
    this.name = 'MalformedCallReports';
  }
}

/** A pending entry that could not be read, with the id the handset can forget it by, if it has one. */
export interface UnreadableCallReport {
  readonly eventId: string | null;
  /** The kind of failure only: the entry itself can hold a caller's number. */
  readonly failure: string;
}

export interface PendingCallReports {
  readonly reports: CallReport[];
  readonly unreadable: UnreadableCallReport[];
}

/**
 * The handset's pending call events, checked before anything is sent.
 *
 * Checked rather than cast: this text was written by another language's code on another thread,
 * and a report the backend would refuse is better caught here with a reason than there without.
 * Checked one entry at a time, so an entry that cannot be read is set aside instead of holding
 * back every report written after it.
 */
export function parseCallReports(document: string): PendingCallReports {
  let parsed: unknown;
  try {
    parsed = JSON.parse(document);
  } catch {
    return nothingReadable(new MalformedCallReports('not JSON'));
  }
  if (!Array.isArray(parsed)) {
    return nothingReadable(new MalformedCallReports('not a list'));
  }
  const pending: PendingCallReports = { reports: [], unreadable: [] };
  for (const entry of parsed) {
    try {
      pending.reports.push(readReport(entry));
    } catch (error) {
      if (!(error instanceof MalformedCallReports)) {
        throw error;
      }
      pending.unreadable.push({
        eventId: eventIdOf(entry),
        failure: error.name,
      });
    }
  }
  return pending;
}

function nothingReadable(failure: MalformedCallReports): PendingCallReports {
  return {
    reports: [],
    unreadable: [{ eventId: null, failure: failure.name }],
  };
}

function eventIdOf(entry: unknown): string | null {
  if (typeof entry !== 'object' || entry === null) {
    return null;
  }
  const id = (entry as Record<string, unknown>).event_id;
  return typeof id === 'string' ? id : null;
}

function readReport(value: unknown): CallReport {
  if (typeof value !== 'object' || value === null) {
    throw new MalformedCallReports('an entry is not an object');
  }
  const entry = value as Record<string, unknown>;
  const kind = oneOf(entry.kind, KINDS, 'kind');
  const report: CallReport = {
    event_id: text(entry.event_id, 'event_id'),
    call_id: text(entry.call_id, 'call_id'),
    kind,
    occurred_at: text(entry.occurred_at, 'occurred_at'),
  };
  if (entry.caller_number !== undefined) {
    report.caller_number = text(entry.caller_number, 'caller_number');
  }
  if (entry.screening !== undefined) {
    report.screening = oneOf(entry.screening, DECISIONS, 'screening');
  }
  if (entry.ending !== undefined) {
    report.ending = oneOf(entry.ending, ENDINGS, 'ending');
  }
  return report;
}

function text(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new MalformedCallReports(`${field} is missing`);
  }
  return value;
}

function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  field: string,
): T {
  const found = allowed.find(candidate => candidate === value);
  if (found === undefined) {
    throw new MalformedCallReports(`${field} is not one the backend accepts`);
  }
  return found;
}
