import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { Dial, DialCaption, DialFigure } from '../../components/Dial';
import { Hero } from '../../components/Hero';
import { Icon } from '../../components/icon/Icon';
import { ringParts, tally } from '../../home/today';
import type { HomeSnapshot } from '../../home/useHome';
import { theme } from '../../theme';

/** Today's calls on the ring, with a legend of what became of them. */
export function Today({
  snapshot,
  screeningOnly,
}: {
  readonly snapshot: HomeSnapshot;
  readonly screeningOnly: boolean;
}): React.JSX.Element {
  const { t } = useTranslation();
  const counts = tally(snapshot.today);
  const parts = ringParts(counts);
  const figure = screeningOnly ? counts.turnedAway : counts.total;

  const legend = screeningOnly
    ? [
        {
          key: 'you',
          count: counts.you,
          label: t('home.rangYou'),
          colour: theme.colour.needsYou,
        },
        {
          key: 'turnedAway',
          count: counts.turnedAway,
          label: t('home.stopped'),
          colour: theme.colour.textFaint,
        },
      ]
    : [
        {
          key: 'handled',
          count: counts.handled,
          label: t('home.handled'),
          colour: theme.colour.accent,
        },
        {
          key: 'you',
          count: counts.you,
          label: t('home.you'),
          colour: theme.colour.needsYou,
        },
        {
          key: 'turnedAway',
          count: counts.turnedAway,
          label: t('home.turnedAway'),
          colour: theme.colour.textFaint,
        },
      ];

  return (
    <>
      <Hero testID="home-today">
        <View style={styles.hero}>
          <Dial
            size={210}
            blank={counts.total === 0}
            rest={theme.colour.dialRestOnHero}
            segments={[
              { fraction: parts.handled, colour: theme.colour.accent },
              { fraction: parts.you, colour: theme.colour.needsYou },
              { fraction: parts.turnedAway, colour: theme.colour.textFaint },
            ]}
          >
            <Icon
              name={screeningOnly ? 'shield' : 'bot'}
              colour={theme.colour.accentDeep}
              size={26}
            />
            <DialFigure text={String(figure)} testID="home-figure" />
            <DialCaption
              text={t(screeningOnly ? 'home.stoppedToday' : 'home.callsToday')}
            />
          </Dial>
          {counts.total > 0 ? (
            <View style={styles.legend} testID="home-legend">
              {legend.map(item => (
                <View key={item.key} style={styles.legendItem}>
                  <View
                    style={[styles.dot, { backgroundColor: item.colour }]}
                  />
                  <Text style={styles.legendText}>
                    <Text style={styles.legendCount}>{item.count}</Text>{' '}
                    {item.label}
                  </Text>
                </View>
              ))}
            </View>
          ) : (
            <Text style={styles.nothing}>{t('home.nothingYet')}</Text>
          )}
        </View>
      </Hero>
    </>
  );
}

const styles = StyleSheet.create({
  hero: {
    alignItems: 'center',
    gap: theme.space.sm,
    paddingVertical: theme.space.sm,
  },
  legend: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: theme.space.md,
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.xs,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  legendText: { ...theme.type.subtitle, color: theme.colour.heroMuted },
  legendCount: { fontFamily: theme.font.strong, color: theme.colour.heroText },
  nothing: { ...theme.type.subtitle, color: theme.colour.heroMuted },
});
