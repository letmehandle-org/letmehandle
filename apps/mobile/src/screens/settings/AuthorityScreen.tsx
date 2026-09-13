import React from 'react';
import { useTranslation } from 'react-i18next';

import { Card } from '../../components/Card';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { Toggle } from '../../components/Toggle';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { CAPABILITIES, CAPABILITY_ICONS } from '../../preferences/options';
import { useImmediateSave } from '../../preferences/useImmediateSave';

interface Props {
  readonly onBack: () => void;
}

/**
 * What the assistant may do on somebody's behalf.
 *
 * Every capability is its own switch and every one starts off: these are things said to
 * strangers on the telephone, and anything left off makes the assistant fetch the user instead.
 */
export function AuthorityScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, save } = useImmediateSave();
  const granted = preferences.authority.capabilities ?? [];

  return (
    <Screen
      onBack={onBack}
      title={t('settings.authority')}
      scrollable
      testID="settings-authority"
    >
      <Card>
        {CAPABILITIES.map((capability, position) => (
          <Toggle
            key={capability}
            icon={CAPABILITY_ICONS[capability]}
            label={t(`preferences.capability.${capability}`)}
            value={granted.includes(capability)}
            onChange={on => {
              save({
                authority: {
                  capabilities: on
                    ? [...granted, capability]
                    : granted.filter(entry => entry !== capability),
                },
              });
            }}
            last={position === CAPABILITIES.length - 1}
            testID={`capability-${capability}`}
          />
        ))}
      </Card>
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}
