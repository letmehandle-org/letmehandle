import React from 'react';
import { useTranslation } from 'react-i18next';

import { HoursEditor } from '../../components/HoursEditor';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { useImmediateSave } from '../../preferences/useImmediateSave';

interface Props {
  readonly onBack: () => void;
}

/**
 * When the assistant answers: around the clock, or one window of the day (D-027).
 *
 * Every change is saved as it is made, like every other settings page, and a refused one is put
 * back by the provider and explained here.
 */
export function HoursScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, save } = useImmediateSave();

  return (
    <Screen
      onBack={onBack}
      title={t('hours.title')}
      scrollable
      testID="settings-hours"
    >
      <HoursEditor
        value={preferences.hours.active ?? null}
        onChange={active => {
          save({ hours: { active } });
        }}
      />
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}
