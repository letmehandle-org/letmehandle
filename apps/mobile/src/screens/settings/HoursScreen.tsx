import React from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '../../components/Button';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { usePreferences } from '../../preferences/PreferencesProvider';
import {
  AROUND_THE_CLOCK,
  answersAroundTheClock,
} from '../../preferences/rules';
import { useImmediateSave } from '../../preferences/useImmediateSave';
import { AroundTheClock } from '../SetupScreen';

interface Props {
  readonly onBack: () => void;
}

/**
 * When the assistant works: around the clock, unless hours are set.
 *
 * Setting a single window of hours waits on the backend, which today stores working and quiet
 * hours instead. Somebody who already has those is told they still apply and can go back to
 * around the clock; nobody is offered a window the API cannot store.
 */
export function HoursScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, busy, save } = useImmediateSave();
  const always = answersAroundTheClock(preferences.hours);

  return (
    <Screen
      onBack={onBack}
      title={t('hours.title')}
      scrollable
      testID="settings-hours"
    >
      {always ? (
        <AroundTheClock />
      ) : (
        <>
          <Notice message={t('hours.windows')} testID="hours-windows" />
          <Button
            label={t('hours.useAlways')}
            variant="ghost"
            icon="infinity"
            busy={busy}
            onPress={() => {
              save({ hours: AROUND_THE_CLOCK });
            }}
            testID="hours-use-always"
          />
        </>
      )}
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}
