import React from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Text } from 'react-native';

import { useSession } from '../auth/SessionProvider';
import { useCallScreening } from '../calls/CallScreeningProvider';
import { Button } from '../components/Button';
import { CallRow } from '../components/CallRow';
import { Card } from '../components/Card';
import { Dial } from '../components/Dial';
import { Hero } from '../components/Hero';
import { Icon } from '../components/icon/Icon';
import { Notice } from '../components/Notice';
import { Row } from '../components/Row';
import { Screen } from '../components/Screen';
import { StatusPill } from '../components/StatusPill';
import { timeOfDay } from '../history/presentation';
import { homeState, type HomeState } from '../home/today';
import { useHome, type HomeSnapshot } from '../home/useHome';
import { useSecureScreen } from '../security/secureScreen';
import { NeedsYou } from './home/NeedsYou';
import { Today } from './home/Today';
import { theme } from '../theme';

interface Props {
  readonly onOpenCall?: (callId: string) => void;
  readonly onOpenEscalation?: (callId: string) => void;
  readonly onOpenScreening?: () => void;
}

const PILL: Record<
  HomeState,
  { key: string; tone: 'assistant' | 'needsYou' | 'quiet' }
> = {
  'needs-you': { key: 'home.needsYou', tone: 'needsYou' },
  'not-on-duty': { key: 'home.notOnDuty', tone: 'quiet' },
  screening: { key: 'home.screening', tone: 'assistant' },
  'on-duty': { key: 'home.onDuty', tone: 'assistant' },
  'first-day': { key: 'home.waiting', tone: 'quiet' },
};

/**
 * One object, one number, a few rows: whether the assistant is taking calls, how today went, and
 * the latest calls.
 *
 * Every state is one the facts put it in (see `home/today.ts`). A call that is still going and has
 * been escalated takes the whole screen, in the one colour that only ever means the user is wanted.
 */
export function HomeScreen({
  onOpenCall = () => undefined,
  onOpenEscalation = () => undefined,
  onOpenScreening = () => undefined,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { api, profile } = useSession();
  const { screening } = useCallScreening();
  const { snapshot, stale, refresh } = useHome(
    api,
    screening,
    profile?.id ?? null,
  );

  return (
    <Screen scrollable insideTabs testID="home-screen">
      {snapshot === null ? (
        <Loading failed={stale} onRetry={refresh} />
      ) : (
        <Loaded
          snapshot={snapshot}
          stale={stale}
          onOpenCall={onOpenCall}
          onOpenEscalation={onOpenEscalation}
          onOpenScreening={onOpenScreening}
          callsForwarded={Boolean(profile?.call_forwarding)}
        />
      )}
      {snapshot !== null && stale && (
        <Notice
          tone="problem"
          message={t('home.offline', {
            time: timeOfDay(snapshot.at.toISOString()),
          })}
          testID="home-offline"
        />
      )}
    </Screen>
  );
}

/** The ring first, because its shape never changes; only the numbers are unknown. */
function Loading({
  failed,
  onRetry,
}: {
  readonly failed: boolean;
  readonly onRetry: () => void;
}): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <>
      <Hero testID="home-loading">
        <Dial size={210} blank>
          {failed ? (
            <Icon name="wifi-off" colour={theme.colour.heroMuted} size={30} />
          ) : (
            <ActivityIndicator color={theme.colour.accent} />
          )}
        </Dial>
      </Hero>
      {failed && (
        <>
          <Notice
            tone="problem"
            message={t('home.loadFailed')}
            testID="home-problem"
          />
          <Button
            label={t('common.tryAgain')}
            variant="ghost"
            onPress={onRetry}
            testID="home-retry"
          />
        </>
      )}
    </>
  );
}

function Loaded({
  snapshot,
  stale,
  onOpenCall,
  onOpenEscalation,
  onOpenScreening,
  callsForwarded,
}: {
  readonly snapshot: HomeSnapshot;
  readonly stale: boolean;
  readonly onOpenCall: (callId: string) => void;
  readonly onOpenEscalation: (callId: string) => void;
  readonly onOpenScreening: () => void;
  readonly callsForwarded: boolean;
}): React.JSX.Element {
  const { t } = useTranslation();
  const state = homeState({
    callsForwarded,
    escalatedCallId: snapshot.escalation?.callId ?? null,
    screeningRole: snapshot.screeningRole,
    anyCalls: snapshot.latest.length > 0,
  });
  const pill = PILL[state];

  return (
    <>
      <StatusPill label={t(pill.key)} tone={pill.tone} testID="home-status" />
      {snapshot.latest.length > 0 && <KeptOutOfScreenshots />}

      {state === 'needs-you' && snapshot.escalation !== null ? (
        <NeedsYou
          snapshot={snapshot}
          onOpen={() => {
            onOpenEscalation(snapshot.escalation?.callId ?? '');
          }}
        />
      ) : (
        <Today snapshot={snapshot} screeningOnly={state === 'screening'} />
      )}

      {state === 'not-on-duty' && (
        <>
          <Card>
            <Row
              icon="phone-ring"
              tone="needsYou"
              title={t('home.ringsDirectly')}
              subtitle={t('home.notScreening')}
              last
              testID="home-not-screening"
            />
          </Card>
          <Button
            label={t('home.turnOnScreening')}
            icon="shield"
            onPress={onOpenScreening}
            testID="home-turn-on-screening"
          />
        </>
      )}
      {state === 'screening' && (
        <Text style={styles.note} testID="home-screening-only">
          {t('home.screeningOnly')}
        </Text>
      )}

      {state === 'first-day' && !stale && (
        <>
          <Text style={styles.label}>{t('home.whenItDoes')}</Text>
          <Card>
            <Row icon="check" tone="assistant" title={t('home.fillsRing')} />
            <Row
              icon="phone-ring"
              tone="needsYou"
              title={t('home.ringsYou')}
              last
            />
          </Card>
        </>
      )}

      {snapshot.latest.length > 0 && (
        <>
          <Text style={styles.label}>{t('home.latest')}</Text>
          <Card>
            {snapshot.latest.map((call, index) => (
              <CallRow
                key={call.id}
                call={call}
                last={index === snapshot.latest.length - 1}
                onOpen={onOpenCall}
                testID={`home-call-${call.id}`}
              />
            ))}
          </Card>
        </>
      )}
    </>
  );
}

/** Mounted only while callers' names and headlines are on the screen (D-035). */
function KeptOutOfScreenshots(): null {
  useSecureScreen();
  return null;
}

const styles = StyleSheet.create({
  note: { ...theme.type.subtitle, color: theme.colour.textMuted },
  label: {
    ...theme.type.label,
    color: theme.colour.textFaint,
    marginTop: theme.space.sm,
    marginLeft: theme.space.sm,
  },
});
