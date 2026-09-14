import React from 'react';
import { useTranslation } from 'react-i18next';

import type { CallSummary } from '@letmehandle/api-client';

import { iconOf, timeOfDay, toneOf } from '../history/presentation';
import { callerName, listLine } from '../history/words';
import { Row } from './Row';

/** One call in a list: who, what happened and when, opening its summary. */
export function CallRow({
  call,
  last,
  onOpen,
  testID,
}: {
  readonly call: CallSummary;
  readonly last: boolean;
  readonly onOpen: (callId: string) => void;
  readonly testID: string;
}): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <Row
      icon={iconOf(call.caller, call.outcome)}
      tone={toneOf(call)}
      title={callerName(call.caller, t)}
      subtitle={listLine(call, t)}
      value={timeOfDay(call.started_at)}
      onPress={() => {
        onOpen(call.id);
      }}
      last={last}
      testID={testID}
    />
  );
}
