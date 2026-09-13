import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { ERROR_CODES, type TranscriptLine } from '@letmehandle/api-client';

import { ApiError } from '../../api/errors';
import { describeFailure } from '../../api/messages';
import { useSession } from '../../auth/SessionProvider';
import { Button } from '../../components/Button';
import { Disc } from '../../components/Disc';
import { Icon } from '../../components/icon/Icon';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { dayAndMonth } from '../../history/presentation';
import { useLoaded } from '../../history/useLoaded';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { useSecureScreen } from '../../security/secureScreen';
import { theme } from '../../theme';

interface Props {
  readonly callId: string;
  readonly onBack: () => void;
  readonly onOpenPrivacy: () => void;
}

/**
 * What was said, in the order it was said, and when it goes.
 *
 * Purged and never recorded are different answers and each gets its own: deletion is the user's
 * retention working, not something missing, and a call nobody spoke on never had words to keep.
 */
export function TranscriptScreen({
  callId,
  onBack,
  onOpenPrivacy,
}: Props): React.JSX.Element {
  useSecureScreen();
  const { t, i18n } = useTranslation();
  const { api } = useSession();
  const { preferences } = usePreferences();
  const load = useCallback(() => api.transcript(callId), [api, callId]);
  const { loaded, retry } = useLoaded(load);

  const code =
    loaded.state === 'failed' && loaded.error instanceof ApiError
      ? loaded.error.code
      : null;
  const days = preferences.privacy?.transcript_retention_days ?? 7;

  return (
    <Screen
      onBack={onBack}
      title={t('transcript.title')}
      scrollable
      testID="transcript-screen"
    >
      {loaded.state === 'loading' && (
        <ActivityIndicator color={theme.colour.accent} style={styles.loading} />
      )}

      {code === ERROR_CODES.transcriptPurged && (
        <View style={styles.gone} testID="transcript-purged">
          <Disc icon="clock" tone="quiet" size={72} />
          <Text style={styles.goneText}>
            {days === 1
              ? t('transcript.purgedOne')
              : t('transcript.purged', { days })}
          </Text>
          <Button
            label={t('transcript.backToSummary')}
            variant="ghost"
            onPress={onBack}
          />
          <Button
            label={t('transcript.changeHowLong')}
            variant="quiet"
            onPress={onOpenPrivacy}
            testID="transcript-open-privacy"
          />
        </View>
      )}
      {code === ERROR_CODES.transcriptNotRecorded && (
        <View style={styles.gone} testID="transcript-not-recorded">
          <Disc icon="mic" tone="quiet" size={72} />
          <Text style={styles.goneText}>{t('transcript.notRecorded')}</Text>
        </View>
      )}
      {loaded.state === 'failed' &&
        code !== ERROR_CODES.transcriptPurged &&
        code !== ERROR_CODES.transcriptNotRecorded && (
          <>
            <Notice
              tone="problem"
              message={describeFailure(loaded.error, t, {
                refused: t('transcript.loadFailed'),
              })}
              testID="transcript-problem"
            />
            <Button
              label={t('common.tryAgain')}
              variant="ghost"
              onPress={retry}
              testID="transcript-retry"
            />
          </>
        )}

      {loaded.state === 'ready' && (
        <>
          {loaded.value.transcript_expires_at !== null && (
            <Notice
              message={t('transcript.deletesOn', {
                date: dayAndMonth(
                  loaded.value.transcript_expires_at,
                  i18n.language,
                ),
              })}
              testID="transcript-expiry"
            />
          )}
          {loaded.value.entries.map((line, index) => (
            <Line key={`${line.said_at}-${index}`} line={line} />
          ))}
        </>
      )}
    </Screen>
  );
}

function Line({ line }: { readonly line: TranscriptLine }): React.JSX.Element {
  const { t } = useTranslation();
  const assistant = line.speaker === 'agent';
  return (
    <View
      style={styles.line}
      accessible
      accessibilityLabel={`${t(`transcript.speaker.${line.speaker}`)}: ${
        line.text
      }`}
      testID={`transcript-line-${line.speaker}`}
    >
      <View style={styles.who}>
        <Icon
          name={
            assistant ? 'speaker' : line.speaker === 'human' ? 'phone' : 'user'
          }
          size={16}
          colour={assistant ? theme.colour.accentDeep : theme.colour.textMuted}
        />
      </View>
      <View
        style={[styles.bubble, assistant ? styles.assistant : styles.other]}
      >
        <Text style={[styles.text, !assistant && styles.otherText]}>
          {line.text}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  loading: { marginTop: theme.space.xl },
  gone: {
    alignItems: 'center',
    gap: theme.space.md,
    paddingTop: theme.space.xl,
  },
  goneText: {
    ...theme.type.body,
    color: theme.colour.textMuted,
    textAlign: 'center',
  },
  line: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: theme.space.row,
  },
  who: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colour.quietWash,
  },
  bubble: {
    flexShrink: 1,
    paddingVertical: theme.space.row,
    paddingHorizontal: theme.space.md,
    borderRadius: theme.radius.md,
  },
  assistant: { backgroundColor: theme.colour.accentWash },
  other: { backgroundColor: theme.colour.surface, ...theme.shadow.card },
  text: { ...theme.type.body, color: theme.colour.text },
  otherText: { color: theme.colour.textMuted },
});
