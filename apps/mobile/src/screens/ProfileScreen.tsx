import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { useSession } from '../auth/SessionProvider';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { Screen } from '../components/Screen';
import { theme } from '../theme';

export function ProfileScreen(): React.JSX.Element {
  const { t } = useTranslation();
  const { profile, api, refreshProfile, signOut } = useSession();

  const [name, setName] = useState(profile?.display_name ?? '');
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
    <Screen title={t('profile.title')} testID="profile-screen">
      <View style={styles.readOnly}>
        <Text style={styles.label}>{t('profile.number')}</Text>
        <Text style={styles.value} testID="profile-number">
          {profile?.phone_number ?? ''}
        </Text>
      </View>

      <Field
        label={t('profile.name')}
        placeholder={t('profile.namePlaceholder')}
        value={name}
        onChangeText={text => {
          setName(text);
          setSaved(false);
        }}
        problem={problem}
        testID="profile-name-input"
      />

      {saved && (
        <Text
          accessibilityLiveRegion="polite"
          style={styles.saved}
          testID="profile-saved"
        >
          {t('profile.saved')}
        </Text>
      )}

      <Button
        label={t('profile.save')}
        onPress={save}
        busy={busy}
        testID="profile-save"
      />
      <Button
        label={t('profile.signOut')}
        variant="quiet"
        onPress={() => {
          signOut().catch(() => undefined);
        }}
        testID="profile-sign-out"
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  readOnly: { gap: theme.space.xs },
  label: { ...theme.type.body, fontSize: 14, color: theme.colour.textMuted },
  value: { ...theme.type.body, color: theme.colour.text },
  saved: { ...theme.type.body, fontSize: 14, color: theme.colour.textMuted },
});
