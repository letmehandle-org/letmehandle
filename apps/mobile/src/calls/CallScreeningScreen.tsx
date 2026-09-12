import React from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Text } from 'react-native';

import { Button } from '../components/Button';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { theme } from '../theme';
import type { CallScreening } from './callScreening';
import { useCallScreening } from './CallScreeningProvider';
import { useScreeningSetup, type RoleState } from './useScreeningSetup';

/**
 * Call screening, explained before it is asked for.
 *
 * The role is a real grant — the phone will consult this app before it rings — so the screen says
 * what that means and, as plainly, what it does not: no listening, no answering, no assistant on
 * this path. Refusing is a complete answer: the screen says what is then true, which is that
 * every call rings as it would have anyway, and leaves the button where it was.
 *
 * Reached only where the handset has a screening service, so it takes one as given.
 */
export function CallScreeningScreen({
  screening,
}: {
  readonly screening: CallScreening;
}): React.JSX.Element {
  const { t } = useTranslation();
  const { rulesNotSaved } = useCallScreening();
  const setup = useScreeningSetup(screening, {
    title: t('screening.activityRationaleTitle'),
    message: t('screening.activityRationale'),
    accept: t('screening.activityRationaleAccept'),
  });

  return (
    <Screen
      title={t('screening.title')}
      subtitle={t('screening.subtitle')}
      scrollable
      testID="screening-screen"
    >
      <Text style={styles.body}>{t('screening.what')}</Text>
      <Text style={styles.body}>{t('screening.whatNot')}</Text>

      <RoleSection
        role={setup.role}
        onTurnOn={() => {
          setup.requestRole().catch(() => undefined);
        }}
      />

      {rulesNotSaved && (
        <Notice
          tone="problem"
          message={t('screening.rulesNotSaved')}
          testID="screening-rules-not-saved"
        />
      )}

      {setup.role.status === 'held' && (
        <>
          <Text accessibilityRole="header" style={styles.heading}>
            {t('screening.activityTitle')}
          </Text>
          <Text style={styles.body}>{t('screening.activityWhat')}</Text>
          {setup.callActivity === 'granted' ? (
            <Notice
              message={t('screening.activityGranted')}
              testID="screening-activity-granted"
            />
          ) : (
            <>
              <Notice
                message={t('screening.activityOff')}
                testID="screening-activity-off"
              />
              <Button
                label={t('screening.activityTurnOn')}
                variant="quiet"
                onPress={() => {
                  setup.requestCallActivity().catch(() => undefined);
                }}
                testID="screening-activity-turn-on"
              />
            </>
          )}
        </>
      )}
    </Screen>
  );
}

function RoleSection({
  role,
  onTurnOn,
}: {
  readonly role: RoleState;
  readonly onTurnOn: () => void;
}): React.JSX.Element {
  const { t } = useTranslation();

  switch (role.status) {
    case 'checking':
      return (
        <ActivityIndicator
          color={theme.colour.accent}
          testID="screening-checking"
        />
      );
    case 'held':
      return <Notice message={t('screening.held')} testID="screening-held" />;
    case 'unavailable':
      return (
        <Notice
          message={t('screening.unavailable')}
          testID="screening-unavailable"
        />
      );
    case 'failed':
      return (
        <Notice
          tone="problem"
          message={t('screening.failed')}
          testID="screening-failed"
        />
      );
    case 'available':
      return (
        <>
          <Notice
            message={
              role.declined
                ? t('screening.declined')
                : t('screening.offExplained')
            }
            testID={role.declined ? 'screening-declined' : 'screening-off'}
          />
          {role.declined && <Notice message={t('screening.notAskedAgain')} />}
          <Button
            label={t('screening.turnOn')}
            onPress={onTurnOn}
            testID="screening-turn-on"
          />
        </>
      );
  }
}

const styles = StyleSheet.create({
  body: { ...theme.type.body, color: theme.colour.text },
  heading: { ...theme.type.body, fontWeight: '600', color: theme.colour.text },
});
