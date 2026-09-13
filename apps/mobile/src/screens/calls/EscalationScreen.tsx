import React, { useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { useSession } from '../../auth/SessionProvider';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { Dial } from '../../components/Dial';
import { Disc } from '../../components/Disc';
import { Hero } from '../../components/Hero';
import { Icon } from '../../components/icon/Icon';
import { Notice } from '../../components/Notice';
import { Row } from '../../components/Row';
import { Screen } from '../../components/Screen';
import { timeOfDay } from '../../history/presentation';
import { useLoaded } from '../../history/useLoaded';
import { useSecureScreen } from '../../security/secureScreen';
import { theme } from '../../theme';

/** How often a live escalation is read again, so it follows the call to its end. */
export const ESCALATION_REFRESH_MS = 5_000;

interface Props {
  readonly callId: string;
  readonly onBack: () => void;
  readonly onOpenSummary: (callId: string) => void;
}

/**
 * Why the assistant wants the user on a call, in the words the notification carried.
 *
 * While the call is live it says what stopped the assistant, what it knows and what it needs, and
 * that answering the phone is how to join — this app places no calls. Opened after the call has
 * ended, the same route shows that it finished and leads to the summary instead (D-016).
 */
export function EscalationScreen({
  callId,
  onBack,
  onOpenSummary,
}: Props): React.JSX.Element {
  useSecureScreen();
  const { t } = useTranslation();
  const { api } = useSession();
  const load = useCallback(() => api.escalation(callId), [api, callId]);
  const { loaded, retry, refresh } = useLoaded(load);

  const escalation = loaded.state === 'ready' ? loaded.value : null;
  const live = escalation?.status === 'live';

  // Opened while the phone rings, the screen stays open through the call: read once, it would go
  // on asking the user to answer a call that has already ended.
  useEffect(() => {
    if (!live) {
      return;
    }
    const timer = setInterval(refresh, ESCALATION_REFRESH_MS);
    return () => {
      clearInterval(timer);
    };
  }, [live, refresh]);
  const openSummary = (): void => {
    onOpenSummary(callId);
  };

  return (
    <Screen
      onBack={onBack}
      scrollable
      testID="escalation-screen"
      footer={
        escalation === null ? undefined : live ? (
          <View style={styles.join} testID="escalation-answer">
            <Icon name="phone-in" size={20} colour={theme.colour.accentDeep} />
            <Text style={styles.joinText}>{t('escalation.answerToJoin')}</Text>
          </View>
        ) : (
          <Button
            label={t('escalation.openSummary')}
            variant="ghost"
            onPress={openSummary}
            testID="escalation-open-summary"
          />
        )
      }
    >
      {loaded.state === 'loading' && (
        <ActivityIndicator color={theme.colour.accent} style={styles.loading} />
      )}
      {loaded.state === 'failed' && (
        <>
          <Notice
            tone="problem"
            message={t('escalation.loadFailed')}
            testID="escalation-problem"
          />
          <Button
            label={t('common.tryAgain')}
            variant="ghost"
            onPress={retry}
          />
          <Button
            label={t('escalation.openSummary')}
            variant="quiet"
            onPress={openSummary}
            testID="escalation-fallback-summary"
          />
        </>
      )}
      {escalation !== null && (
        <>
          <View style={styles.status}>
            <Icon
              name={live ? 'phone-ring' : 'check-c'}
              size={22}
              colour={live ? theme.colour.needsYouDeep : theme.colour.textMuted}
            />
            <Text
              style={[styles.statusText, live && styles.liveText]}
              testID="escalation-status"
            >
              {t(live ? 'escalation.live' : 'escalation.ended')}
            </Text>
            <Text style={styles.statusTime}>
              {timeOfDay(escalation.raised_at)}
            </Text>
          </View>

          <Hero tone={live ? 'needsYou' : undefined}>
            <View style={styles.hero}>
              <Dial
                size={150}
                rest={theme.colour.dialRestOnHero}
                segments={
                  live
                    ? [{ fraction: 0.18, colour: theme.colour.needsYou }]
                    : []
                }
              >
                <Disc
                  icon="phone"
                  tone={live ? 'needsYou' : 'quiet'}
                  size={64}
                />
              </Dial>
              <Text style={styles.caller}>
                {escalation.caller ?? escalation.caller_label}
              </Text>
              {escalation.caller !== null && (
                <Text style={styles.callerLabel}>
                  {escalation.caller_label}
                </Text>
              )}
            </View>
          </Hero>

          <Card>
            <Row
              icon="lock"
              tone="needsYou"
              title={escalation.title}
              subtitle={t('escalation.why')}
              testID="escalation-why"
            />
            {escalation.established !== null && (
              <Row
                icon="info"
                title={escalation.established}
                subtitle={t('escalation.knows')}
              />
            )}
            {escalation.needed !== null && (
              <Row
                icon="bubble-q"
                title={escalation.needed}
                subtitle={t('escalation.needs')}
                last
              />
            )}
          </Card>
          {escalation.needed === null && escalation.established === null && (
            <Text style={styles.body}>{escalation.body}</Text>
          )}
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  loading: { marginTop: theme.space.xl },
  status: { flexDirection: 'row', alignItems: 'center', gap: theme.space.sm },
  statusText: { ...theme.type.strong, color: theme.colour.textMuted, flex: 1 },
  liveText: { color: theme.colour.needsYouDeep },
  statusTime: { ...theme.type.strong, color: theme.colour.textMuted },
  hero: {
    alignItems: 'center',
    gap: theme.space.xs,
    paddingVertical: theme.space.md,
  },
  caller: { ...theme.type.heading, color: theme.colour.heroText },
  callerLabel: { ...theme.type.subtitle, color: theme.colour.heroMuted },
  body: { ...theme.type.body, color: theme.colour.textMuted },
  join: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.sm,
    minHeight: 64,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colour.accentWash,
  },
  joinText: { ...theme.type.body, color: theme.colour.accentDeep },
});
