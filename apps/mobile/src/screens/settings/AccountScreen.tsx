import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { useSession } from '../../auth/SessionProvider';
import { Button } from '../../components/Button';
import { Disc } from '../../components/Disc';
import { Field } from '../../components/Field';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { theme } from '../../theme';

interface Props {
  readonly onBack: () => void;
}

/** The account's name and number, and signing out of this phone. */
export function AccountScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { profile, api, refreshProfile, signOut } = useSession();

  const stored = profile?.display_name ?? '';
  const [name, setName] = useState(stored);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const save = (): void => {
    setBusy(true);
    setProblem(null);
    setSaved(false);
    api
      .updateMe({ display_name: name.trim() === '' ? null : name.trim() })
      .then(() => refreshProfile())
      .then(() => {
        setSaved(true);
      })
      .catch(() => {
        setProblem(t('common.somethingWentWrong'));
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <Screen
      onBack={onBack}
      title={t('account.title')}
      scrollable
      testID="profile-screen"
      footer={
        <>
          <Button
            label={t('account.signOut')}
            variant="ghost"
            onPress={() => {
              signOut().catch(() => undefined);
            }}
            testID="profile-sign-out"
          />
          <Text style={styles.devices}>{t('account.otherDevices')}</Text>
        </>
      }
    >
      <View style={styles.identity}>
        <Disc icon="user" tone="assistant" size={80} />
        <Text style={styles.number} testID="profile-number">
          {profile?.phone_number ?? ''}
        </Text>
      </View>

      <Field
        label={t('account.name')}
        placeholder={t('account.namePlaceholder')}
        value={name}
        onChangeText={text => {
          setName(text);
          setSaved(false);
        }}
        problem={problem}
        testID="profile-name-input"
      />
      <Button
        label={t('common.save')}
        onPress={save}
        busy={busy}
        disabled={name.trim() === stored.trim()}
        testID="profile-save"
      />
      {saved && (
        <Notice
          tone="done"
          message={t('account.saved')}
          testID="profile-saved"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  identity: {
    alignItems: 'center',
    gap: theme.space.sm,
    paddingVertical: theme.space.md,
  },
  number: { ...theme.type.subtitle, color: theme.colour.textMuted },
  devices: {
    ...theme.type.caption,
    color: theme.colour.textFaint,
    textAlign: 'center',
  },
});
