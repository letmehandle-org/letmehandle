import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { describeFailure } from '../api/messages';
import { useSession } from '../auth/SessionProvider';
import { Button } from '../components/Button';
import { CallRow } from '../components/CallRow';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { Dial } from '../components/Dial';
import { Icon } from '../components/icon/Icon';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import {
  byDay,
  dayAndMonth,
  FILTERS,
  type Filter,
} from '../history/presentation';
import { useCallPages } from '../history/useCallPages';
import { useSecureScreen } from '../security/secureScreen';
import { theme } from '../theme';

interface Props {
  readonly onOpenCall: (callId: string) => void;
  /** Changes whenever the history may have changed elsewhere, such as a call being deleted. */
  readonly refreshKey?: number;
}

/**
 * Every call, newest first, under the day it happened.
 *
 * Filters are the API's own — an outcome, or whether the user joined — so what a filter shows
 * is exactly what the server counts as that, and nothing is filtered on this side of the wire.
 */
export function ActivityScreen({
  onOpenCall,
  refreshKey = 0,
}: Props): React.JSX.Element {
  useSecureScreen();
  const { t, i18n } = useTranslation();
  const { api } = useSession();
  const [filter, setFilter] = useState<Filter>('all');
  const { list, loadingMore, retry, loadMore } = useCallPages(
    api,
    filter,
    refreshKey,
  );

  const filters = (
    <View
      style={styles.filters}
      accessibilityRole="radiogroup"
      accessibilityLabel={t('activity.filter.label')}
    >
      {FILTERS.map(option => (
        <Chip
          key={option}
          label={t(`activity.filter.${option}`)}
          selected={option === filter}
          onPress={() => {
            setFilter(option);
          }}
          testID={`activity-filter-${option}`}
        />
      ))}
    </View>
  );

  return (
    <Screen
      title={t('activity.title')}
      insideTabs
      scrollable
      testID="activity-screen"
    >
      {filters}
      {list.state === 'failed' && (
        <>
          <Notice
            tone="problem"
            message={describeFailure(list.error, t, {
              refused: t('activity.loadFailed'),
            })}
            testID="activity-problem"
          />
          <Button
            label={t('common.tryAgain')}
            variant="ghost"
            onPress={retry}
            testID="activity-retry"
          />
        </>
      )}
      {list.state === 'ready' && list.value.calls.length === 0 && (
        <View style={styles.empty} testID="activity-empty">
          <Dial size={96} blank>
            <Icon name="activity" colour={theme.colour.textGhost} size={28} />
          </Dial>
          <Text style={styles.emptyText}>
            {t(filter === 'all' ? 'activity.empty' : 'activity.emptyFiltered')}
          </Text>
        </View>
      )}
      {list.state === 'ready' &&
        byDay(list.value.calls, new Date()).map(section => (
          <View key={section.key} style={styles.section}>
            <Text style={styles.label}>
              {section.day.kind === 'date'
                ? dayAndMonth(section.day.date, i18n.language)
                : t(`activity.${section.day.kind}`)}
            </Text>
            <Card>
              {section.calls.map((call, index) => (
                <CallRow
                  key={call.id}
                  call={call}
                  onOpen={onOpenCall}
                  last={index === section.calls.length - 1}
                  testID={`call-${call.id}`}
                />
              ))}
            </Card>
          </View>
        ))}
      {list.state === 'ready' && list.value.cursor !== null && (
        <Button
          label={t('activity.more')}
          variant="quiet"
          busy={loadingMore}
          onPress={loadMore}
          testID="activity-more"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  filters: { flexDirection: 'row', flexWrap: 'wrap', gap: theme.space.sm },
  section: { gap: theme.space.sm },
  label: {
    ...theme.type.label,
    color: theme.colour.textFaint,
    marginTop: theme.space.sm,
    marginLeft: theme.space.sm,
  },
  empty: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.md,
    paddingVertical: theme.space.xl * 2,
  },
  emptyText: {
    ...theme.type.body,
    color: theme.colour.textMuted,
    textAlign: 'center',
  },
});
