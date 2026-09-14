import React from 'react';
import { useTranslation } from 'react-i18next';

import { CallGraph } from '../../components/CallGraph';
import { Card } from '../../components/Card';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { Toggle } from '../../components/Toggle';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { hearsEveryCall, withEveryCall } from '../../preferences/rules';
import { useImmediateSave } from '../../preferences/useImmediateSave';

interface Props {
  readonly onBack: () => void;
}

/** When the phone rings for the user, and the two notifications they can add. */
export function WhenCalledScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, save } = useImmediateSave();
  const notifications = preferences.notifications;

  return (
    <Screen
      onBack={onBack}
      title={t('settings.when')}
      scrollable
      testID="settings-when"
    >
      <CallGraph />
      <Card>
        <Toggle
          icon="bell"
          label={t('calls.everyCall')}
          value={hearsEveryCall(notifications)}
          onChange={on => {
            save({ notifications: withEveryCall(notifications, on) });
          }}
          testID="when-every-call"
        />
        <Toggle
          icon="moon"
          label={t('calls.evening')}
          value={notifications.daily_summary}
          onChange={on => {
            save({ notifications: { ...notifications, daily_summary: on } });
          }}
          last
          testID="when-evening"
        />
      </Card>
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}
