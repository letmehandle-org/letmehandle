import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text } from 'react-native';

import { Card } from '../components/Card';
import { Dial, DialCaption } from '../components/Dial';
import { Hero } from '../components/Hero';
import { Icon } from '../components/icon/Icon';
import { Row } from '../components/Row';
import { Screen } from '../components/Screen';
import { StatusPill } from '../components/StatusPill';
import { theme } from '../theme';

/**
 * Home, as far as the product can honestly fill it today.
 *
 * The ring and the latest calls need call history, which arrives with phases 8 and 11. Until
 * then the ring is drawn blank and the pill says plainly that nothing is being answered yet — a
 * zero with "on duty" beside it would claim an assistant that is not taking calls.
 */
export function HomeScreen(): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <Screen scrollable insideTabs testID="home-screen">
      <StatusPill label={t('home.notYet')} tone="quiet" testID="home-status" />

      <Hero>
        <Dial size={210} blank>
          <Icon name="bot" colour={theme.colour.heroMuted} size={30} />
          <Text style={styles.zero}>0</Text>
          <DialCaption text={t('home.callsToday')} />
        </Dial>
      </Hero>

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
    </Screen>
  );
}

const styles = StyleSheet.create({
  zero: { ...theme.type.figure, color: theme.colour.textGhost },
  label: {
    ...theme.type.label,
    color: theme.colour.textFaint,
    marginTop: theme.space.sm,
    marginLeft: theme.space.sm,
  },
});
