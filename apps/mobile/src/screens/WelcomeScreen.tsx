import React from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '../components/Button';
import { Screen } from '../components/Screen';

interface Props {
  readonly onStart: () => void;
}

export function WelcomeScreen({ onStart }: Props): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <Screen
      title={t('welcome.title')}
      subtitle={t('welcome.subtitle')}
      testID="welcome-screen"
    >
      <Button
        label={t('welcome.start')}
        onPress={onStart}
        testID="start-button"
      />
    </Screen>
  );
}
