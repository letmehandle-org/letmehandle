/** The words a call is described in, from the translations. */
import type { TFunction } from 'i18next';

import type { Caller, CallSummary } from '@letmehandle/api-client';

import { durationParts, isDetailKind } from './presentation';

/** Who called, as the user would name them: the name if known, else the kind of call. */
export function callerName(caller: Caller, t: TFunction): string {
  if (caller.display_name !== null) {
    return caller.display_name;
  }
  if (caller.number_withheld) {
    return t('call.withheld');
  }
  return t(`call.category.${caller.category}`);
}

/** A detail's label in words; one of a kind this app does not know yet is shown as sent. */
export function detailLabel(label: string, t: TFunction): string {
  return isDetailKind(label) ? t(`call.detail.${label}`) : label;
}

export function durationWords(
  seconds: number | null,
  t: TFunction,
): string | null {
  const parts = durationParts(seconds);
  return parts === null
    ? null
    : t(`duration.${parts.key}`, { count: parts.count });
}

/** The line under a call in the list: what happened, then how long it took. */
export function listLine(call: CallSummary, t: TFunction): string {
  const what =
    call.status === 'in_progress'
      ? t('activity.inProgress')
      : call.headline ??
        (call.outcome === null
          ? t('call.noSummary')
          : t(`call.outcome.${call.outcome}`));
  // A refused call was over before it rang, so how long it lasted says nothing.
  const length =
    call.status === 'in_progress' || call.outcome === 'rejected_by_rule'
      ? null
      : durationWords(call.duration_seconds, t);
  return length === null
    ? what
    : t('common.pair', { first: what, second: length });
}
