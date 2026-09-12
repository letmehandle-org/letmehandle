import React from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator } from 'react-native';

import { Button } from '../components/Button';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { theme } from '../theme';
import { VoiceEditor } from './VoiceEditor';
import { useVoiceSettings } from './useVoiceSettings';

/**
 * The voice settings, in whichever of its three states it is in.
 *
 * Not folded into the preference sections, and the reason is the API's: the voice is its own
 * resource with its own routes, because what may be offered depends on the provider a
 * deployment runs rather than on anything stored about this user. Editing it through the
 * section machinery would mean a section whose options come from somewhere else entirely.
 */
export function VoiceScreen(): React.JSX.Element {
  const { t } = useTranslation();
  const { state, reload, choose } = useVoiceSettings();

  return (
    <Screen
      title={t('voice.title')}
      subtitle={t('voice.subtitle')}
      scrollable
      testID="voice-screen"
    >
      {state.status === 'loading' && (
        <ActivityIndicator color={theme.colour.accent} testID="voice-loading" />
      )}

      {state.status === 'unavailable' && (
        <>
          <Notice
            tone="problem"
            message={t('voice.loadFailed')}
            testID="voice-problem"
          />
          <Button
            label={t('common.tryAgain')}
            onPress={reload}
            testID="voice-retry"
          />
        </>
      )}

      {state.status === 'ready' && (
        <VoiceEditor
          catalogue={state.catalogue}
          selection={state.selection}
          onChoose={choose}
        />
      )}
    </Screen>
  );
}
