import React from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '../components/Button';
import { Screen } from '../components/Screen';

interface Props {
  readonly onOpenProfile: () => void;
  readonly onOpenSettings: () => void;
}

/**
 * The application shell.
 *
 * Deliberately almost empty. What Home communicates — assistant state, handled calls,
 * escalations — is phase 9's work, against a design that does not exist yet.
 */
export function HomeScreen({
  onOpenProfile,
  onOpenSettings,
}: Props): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <Screen
      title={t('home.title')}
      subtitle={t('home.subtitle')}
      testID="home-screen"
    >
      <Button
        label={t('home.profile')}
        onPress={onOpenProfile}
        testID="open-profile"
      />
      <Button
        label={t('home.settings')}
        variant="quiet"
        onPress={onOpenSettings}
        testID="open-settings"
      />
    </Screen>
  );
}
