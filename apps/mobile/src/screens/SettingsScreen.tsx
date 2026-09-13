import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text } from 'react-native';

import { useCallScreening } from '../calls/CallScreeningProvider';
import { Card } from '../components/Card';
import { Row } from '../components/Row';
import { Screen } from '../components/Screen';
import { usePreferences } from '../preferences/PreferencesProvider';
import { CAPABILITIES } from '../preferences/options';
import { followsTwoLanes } from '../preferences/rules';
import { theme } from '../theme';

export type SettingsPage =
  | 'who'
  | 'when'
  | 'hours'
  | 'authority'
  | 'say'
  | 'personalise'
  | 'privacy'
  | 'account'
  | 'callScreening';

interface Props {
  readonly onOpen: (page: SettingsPage) => void;
}

/**
 * Every setting, each row showing where it stands.
 *
 * The value on the right answers most questions without opening anything. Privacy is not here:
 * how long transcripts are kept is not yet something the API lets anybody change, and a row for
 * it would be a control that cannot work.
 */
export function SettingsScreen({ onOpen }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const facts = preferences.personality.disclosable_facts ?? [];
  const granted = preferences.authority.capabilities?.length ?? 0;
  // A row only where the handset has a screening service to grant.
  const { screening } = useCallScreening();

  return (
    <Screen
      title={t('settings.title')}
      scrollable
      insideTabs
      testID="settings-screen"
    >
      <Text style={styles.label}>{t('settings.calls')}</Text>
      <Card>
        <Row
          icon="users"
          tone="assistant"
          title={t('settings.who')}
          value={t(
            followsTwoLanes(preferences.call_handling)
              ? 'settings.whoValue'
              : 'settings.whoCustom',
          )}
          onPress={() => {
            onOpen('who');
          }}
          testID="settings-open-who"
        />
        <Row
          icon="phone-ring"
          tone="needsYou"
          title={t('settings.when')}
          value={t('settings.whenValue')}
          onPress={() => {
            onOpen('when');
          }}
          testID="settings-open-when"
        />
        <Row
          icon="clock"
          title={t('settings.hours')}
          value={
            preferences.hours.active == null
              ? t('hours.always')
              : `${preferences.hours.active.start}–${preferences.hours.active.end}`
          }
          onPress={() => {
            onOpen('hours');
          }}
          last={screening === null}
          testID="settings-open-hours"
        />
        {screening !== null && (
          <Row
            icon="shield"
            title={t('screening.settingsRow')}
            onPress={() => {
              onOpen('callScreening');
            }}
            last
            testID="settings-open-call-screening"
          />
        )}
      </Card>

      <Text style={styles.label}>{t('settings.assistant')}</Text>
      <Card>
        <Row
          icon="key"
          title={t('settings.authority')}
          value={t('settings.authorityValue', {
            granted,
            total: CAPABILITIES.length,
          })}
          onPress={() => {
            onOpen('authority');
          }}
          testID="settings-open-authority"
        />
        <Row
          icon="eye-off"
          title={t('settings.say')}
          value={
            facts.length === 0 ? t('settings.sayNothing') : String(facts.length)
          }
          onPress={() => {
            onOpen('say');
          }}
          testID="settings-open-say"
        />
        <Row
          icon="sparkle"
          tone="assistant"
          title={t('settings.personalise')}
          onPress={() => {
            onOpen('personalise');
          }}
          last
          testID="settings-open-personalise"
        />
      </Card>

      <Text style={styles.label}>{t('settings.you')}</Text>
      <Card>
        <Row
          icon="lock"
          title={t('settings.privacy')}
          value={t('privacy.days', {
            count: preferences.privacy.transcript_retention_days,
          })}
          onPress={() => {
            onOpen('privacy');
          }}
          testID="settings-open-privacy"
        />
        <Row
          icon="user"
          title={t('settings.account')}
          onPress={() => {
            onOpen('account');
          }}
          last
          testID="settings-open-account"
        />
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  label: {
    ...theme.type.label,
    color: theme.colour.textFaint,
    marginTop: theme.space.sm,
    marginLeft: theme.space.sm,
  },
});
