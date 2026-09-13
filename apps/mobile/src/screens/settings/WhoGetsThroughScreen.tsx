import React from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '../../components/Button';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { followsTwoLanes, twoLanes } from '../../preferences/rules';
import { useImmediateSave } from '../../preferences/useImmediateSave';
import { WhoGetsThrough } from '../SetupScreen';

interface Props {
  readonly onBack: () => void;
}

/**
 * Who gets through, as the rule itself.
 *
 * Nothing to toggle. The one exception is somebody whose calls were set up another way before
 * the two lanes existed: they are told, and offered the lanes, rather than shown a picture that
 * does not describe their calls.
 */
export function WhoGetsThroughScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, busy, save } = useImmediateSave();
  const differs = !followsTwoLanes(preferences.call_handling);

  return (
    <Screen
      onBack={onBack}
      title={t('settings.who')}
      scrollable
      testID="settings-who"
    >
      <WhoGetsThrough />
      {differs && (
        <>
          <Notice message={t('lanes.differs')} testID="who-differs" />
          <Button
            label={t('lanes.apply')}
            variant="ghost"
            busy={busy}
            onPress={() => {
              save({ call_handling: twoLanes(preferences.call_handling) });
            }}
            testID="who-apply"
          />
        </>
      )}
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}
