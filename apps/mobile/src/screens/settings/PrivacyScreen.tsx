import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text } from 'react-native';

import { useSession } from '../../auth/SessionProvider';
import { Card } from '../../components/Card';
import { ConfirmSheet } from '../../components/ConfirmSheet';
import { Icon } from '../../components/icon/Icon';
import { Notice } from '../../components/Notice';
import { Row } from '../../components/Row';
import { Screen } from '../../components/Screen';
import { Segmented } from '../../components/Segmented';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { useImmediateSave } from '../../preferences/useImmediateSave';
import { theme } from '../../theme';

interface Props {
  readonly onBack: () => void;
}

/** The lengths offered, inside the API's 1 to 90 days. There is no "never": the floor is a day. */
export const RETENTION_DAYS = [1, 7, 30, 90] as const;

/** How long words are kept, that recordings never are (D-013), and deleting the account. */
export function PrivacyScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { api, signOut } = useSession();
  const { preferences } = usePreferences();
  const { problem, save } = useImmediateSave();
  const days = preferences.privacy.transcript_retention_days;
  const offered = RETENTION_DAYS.some(option => option === days)
    ? [...RETENTION_DAYS]
    : [...RETENTION_DAYS, days].sort((a, b) => a - b);

  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteProblem, setDeleteProblem] = useState<string | null>(null);

  const deleteAccount = (): void => {
    setDeleting(true);
    setDeleteProblem(null);
    api
      .deleteAccount()
      // The account is gone, so only this phone's copy of the session is left to clear.
      .then(() => signOut())
      .catch(() => {
        setDeleteProblem(t('deleteAccount.failed'));
        setDeleting(false);
      });
  };

  return (
    <Screen
      onBack={onBack}
      title={t('privacy.title')}
      scrollable
      testID="settings-privacy"
    >
      <Text style={styles.label}>{t('privacy.keepFor')}</Text>
      <Segmented
        label={t('privacy.keepFor')}
        value={String(days)}
        options={offered.map(option => ({
          value: String(option),
          label: t('privacy.days', { count: option }),
        }))}
        onChange={value => {
          save({ privacy: { transcript_retention_days: Number(value) } });
        }}
        testID="privacy-retention"
      />
      <Text style={styles.note}>{t('privacy.summariesStay')}</Text>
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}

      <Text style={styles.label}>{t('privacy.neverKept')}</Text>
      <Card>
        <Row
          icon="mic"
          tone="assistant"
          title={t('privacy.recordings')}
          trailing={
            <Icon name="check" size={20} colour={theme.colour.accentDeep} />
          }
          last
          testID="privacy-recordings"
        />
      </Card>

      <Card>
        <Row
          icon="trash"
          tone="warning"
          title={t('privacy.deleteAccount')}
          destructive
          onPress={() => {
            setConfirming(true);
          }}
          last
          testID="privacy-delete-account"
        />
      </Card>

      <ConfirmSheet
        visible={confirming}
        icon="trash"
        title={t('deleteAccount.title')}
        body={t('deleteAccount.body')}
        confirm={t('deleteAccount.confirm')}
        keep={t('deleteAccount.keep')}
        busy={deleting}
        problem={deleteProblem}
        onConfirm={deleteAccount}
        onKeep={() => {
          setConfirming(false);
          setDeleteProblem(null);
        }}
        testID="delete-account-sheet"
      />
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
  note: {
    ...theme.type.subtitle,
    color: theme.colour.textMuted,
    marginLeft: theme.space.sm,
  },
});
