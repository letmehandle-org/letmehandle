import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import type { CallDetail } from '@letmehandle/api-client';

import { ApiError } from '../../api/errors';
import { describeFailure } from '../../api/messages';
import { useSession } from '../../auth/SessionProvider';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { Chip } from '../../components/Chip';
import { ConfirmSheet } from '../../components/ConfirmSheet';
import { Disc } from '../../components/Disc';
import { Notice } from '../../components/Notice';
import { Row } from '../../components/Row';
import { Screen } from '../../components/Screen';
import {
  dayAndMonth,
  dayOf,
  iconOf,
  offsetFrom,
  timeOfDay,
  toneOf,
} from '../../history/presentation';
import { useLoaded } from '../../api/useLoaded';
import { callerName, detailLabel, durationWords } from '../../history/words';
import { useSecureScreen } from '../../security/secureScreen';
import { theme } from '../../theme';

interface Props {
  readonly callId: string;
  readonly onBack: () => void;
  readonly onOpenTranscript: (callId: string) => void;
  readonly onOpenEscalation: (callId: string) => void;
  /** The call is gone; whoever opened it should show the list without it. */
  readonly onDeleted: () => void;
}

/** One call's summary from the server: who, what they wanted, what was settled and what was said. */
export function CallDetailScreen({
  callId,
  onBack,
  onOpenTranscript,
  onOpenEscalation,
  onDeleted,
}: Props): React.JSX.Element {
  useSecureScreen();
  const { t } = useTranslation();
  const { api } = useSession();
  const load = useCallback(() => api.call(callId), [api, callId]);
  const { loaded, retry } = useLoaded(load);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteProblem, setDeleteProblem] = useState<string | null>(null);

  const call = loaded.state === 'ready' ? loaded.value : null;
  const gone =
    loaded.state === 'failed' &&
    loaded.error instanceof ApiError &&
    loaded.error.status === 404;

  const remove = (): void => {
    setDeleting(true);
    setDeleteProblem(null);
    api
      .deleteCall(callId)
      .then(() => {
        setConfirming(false);
        onDeleted();
      })
      .catch(() => {
        setDeleteProblem(t('call.deleteFailed'));
      })
      .finally(() => {
        setDeleting(false);
      });
  };

  return (
    <Screen
      onBack={onBack}
      scrollable
      testID="call-detail"
      action={
        call !== null && call.status === 'ended'
          ? {
              icon: 'trash',
              label: t('call.delete'),
              onPress: () => {
                setConfirming(true);
              },
              testID: 'call-delete',
            }
          : undefined
      }
    >
      {loaded.state === 'loading' && (
        <ActivityIndicator color={theme.colour.accent} style={styles.loading} />
      )}
      {loaded.state === 'failed' &&
        (gone ? (
          <Notice message={t('call.gone')} testID="call-gone" />
        ) : (
          <>
            <Notice
              tone="problem"
              message={describeFailure(loaded.error, t, {
                refused: t('call.loadFailed'),
              })}
              testID="call-problem"
            />
            <Button
              label={t('common.tryAgain')}
              variant="ghost"
              onPress={retry}
              testID="call-retry"
            />
          </>
        ))}
      {call !== null && (
        <Summary
          call={call}
          onOpenTranscript={() => {
            onOpenTranscript(call.id);
          }}
          onOpenEscalation={() => {
            onOpenEscalation(call.id);
          }}
        />
      )}
      {call !== null && (
        <ConfirmSheet
          visible={confirming}
          icon="trash"
          title={t('call.deleteQuestion')}
          items={[t('call.deleteSummary'), t('call.deleteWords')]}
          confirm={t('call.deleteNow')}
          keep={t('call.keep')}
          busy={deleting}
          problem={deleteProblem}
          onConfirm={remove}
          onKeep={() => {
            setConfirming(false);
            setDeleteProblem(null);
          }}
          testID="call-delete-sheet"
        />
      )}
    </Screen>
  );
}

function Summary({
  call,
  onOpenTranscript,
  onOpenEscalation,
}: {
  readonly call: CallDetail;
  readonly onOpenTranscript: () => void;
  readonly onOpenEscalation: () => void;
}): React.JSX.Element {
  const { t, i18n } = useTranslation();
  const day = dayOf(call.started_at, new Date());
  const when = t('call.when', {
    day:
      day.kind === 'date'
        ? dayAndMonth(day.date, i18n.language)
        : t(`activity.${day.kind}`),
    time: timeOfDay(call.started_at),
  });
  const length = durationWords(call.duration_seconds, t);
  const live = call.status === 'in_progress';
  const refused = call.outcome === 'rejected_by_rule';
  const hadWords = call.transcript_available || call.handling === 'assistant';

  return (
    <>
      <View style={styles.identity}>
        <Disc
          icon={iconOf(call.caller, call.outcome)}
          tone={toneOf(call)}
          size={64}
        />
        <View style={styles.identityText}>
          <Text accessibilityRole="header" style={styles.name}>
            {callerName(call.caller, t)}
          </Text>
          <Text style={styles.when} testID="call-when">
            {length === null
              ? when
              : t('common.pair', { first: when, second: length })}
          </Text>
        </View>
      </View>

      <Text style={styles.headline} testID="call-headline">
        {call.headline ?? t(live ? 'call.inProgress' : 'call.noSummary')}
      </Text>

      {(call.outcome !== null ||
        call.intent !== null ||
        call.importance !== null) && (
        <View style={styles.chips}>
          {call.outcome !== null && (
            <Chip
              emphasis
              icon="check"
              label={t(`call.outcome.${call.outcome}`)}
              testID="call-outcome"
            />
          )}
          {call.intent !== null && (
            <Chip label={t(`call.intent.${call.intent}`)} />
          )}
          {call.importance !== null && (
            <Chip label={t(`call.importance.${String(call.importance)}`)} />
          )}
        </View>
      )}

      {live && call.timings.escalated_at !== null && (
        <Button
          label={t('call.needsYou')}
          icon="phone-ring"
          onPress={onOpenEscalation}
          testID="call-open-escalation"
        />
      )}

      {call.human_joined && <Timeline call={call} />}

      {(call.details.length > 0 || call.escalation_reason !== null) && (
        <Card>
          {call.details.map((detail, index) => (
            <Row
              key={`${detail.label}-${index}`}
              title={detail.value}
              subtitle={detailLabel(detail.label, t)}
              last={
                index === call.details.length - 1 &&
                call.escalation_reason === null
              }
            />
          ))}
          {call.escalation_reason !== null && (
            <Row
              icon="phone-ring"
              tone="needsYou"
              title={t(`call.reason.${call.escalation_reason}`)}
              subtitle={t('call.whyItCalled')}
              last
              testID="call-reason"
            />
          )}
        </Card>
      )}

      {hadWords ? (
        <Card>
          <Row
            icon="doc"
            title={t('call.transcript')}
            subtitle={
              call.transcript_expires_at === null
                ? undefined
                : t('call.keptUntil', {
                    date: dayAndMonth(
                      call.transcript_expires_at,
                      i18n.language,
                    ),
                  })
            }
            onPress={onOpenTranscript}
            last
            testID="call-open-transcript"
          />
        </Card>
      ) : (
        !live && (
          <Text style={styles.nothing} testID="call-nothing-kept">
            {refused ? t('call.refusedNothing') : t('call.notKept')}
          </Text>
        )
      )}
    </>
  );
}

/** When a person joined: the call's moments on one strip, as offsets from the start. */
function Timeline({ call }: { readonly call: CallDetail }): React.JSX.Element {
  const { t } = useTranslation();
  const start = call.timings.received_at;
  const moments: { key: string; at: string | null; label: string }[] = [
    {
      key: 'answered',
      at: call.timings.answered_at,
      label: t('call.timeline.answered'),
    },
    {
      key: 'escalated',
      at: call.timings.escalated_at,
      label: t('call.timeline.calledYou'),
    },
    {
      key: 'joined',
      at: call.timings.human_joined_at,
      label: t('call.timeline.youIn'),
    },
    {
      key: 'ended',
      at: call.timings.ended_at,
      label: t('call.timeline.ended'),
    },
  ];
  return (
    <Card>
      <View style={styles.timeline} testID="call-timeline">
        {moments
          .filter(moment => moment.at !== null)
          .map((moment, index) => (
            <View
              key={moment.key}
              style={styles.moment}
              testID={`call-timeline-${moment.key}`}
            >
              <Text style={styles.momentTime}>
                {index === 0 || moment.key === 'ended'
                  ? timeOfDay(moment.at as string)
                  : offsetFrom(start, moment.at as string)}
              </Text>
              <Text style={styles.momentLabel}>{moment.label}</Text>
            </View>
          ))}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  loading: { marginTop: theme.space.xl },
  identity: { flexDirection: 'row', alignItems: 'center', gap: theme.space.md },
  identityText: { flex: 1, gap: 2 },
  name: { ...theme.type.heading, color: theme.colour.text },
  when: { ...theme.type.subtitle, color: theme.colour.textMuted },
  headline: {
    ...theme.type.heading,
    fontFamily: theme.font.regular,
    color: theme.colour.text,
  },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: theme.space.sm },
  timeline: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    padding: theme.space.md,
  },
  moment: { alignItems: 'center', gap: 2 },
  momentTime: { ...theme.type.strong, color: theme.colour.text },
  momentLabel: { ...theme.type.caption, color: theme.colour.textMuted },
  nothing: {
    ...theme.type.subtitle,
    color: theme.colour.textMuted,
    textAlign: 'center',
  },
});
