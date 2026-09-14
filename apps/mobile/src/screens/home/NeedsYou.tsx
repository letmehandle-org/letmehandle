import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '../../components/Button';
import { Dial } from '../../components/Dial';
import { Disc } from '../../components/Disc';
import { Hero } from '../../components/Hero';
import { elapsed } from '../../home/today';
import type { HomeSnapshot } from '../../home/useHome';
import { theme } from '../../theme';

/** A call still going that needs the user, taking the whole of Home. */
export function NeedsYou({
  snapshot,
  onOpen,
}: {
  readonly snapshot: HomeSnapshot;
  readonly onOpen: () => void;
}): React.JSX.Element {
  const { t } = useTranslation();
  const detail = snapshot.escalation?.detail ?? null;
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = setInterval(() => {
      setNow(new Date());
    }, 1000);
    return () => {
      clearInterval(tick);
    };
  }, []);

  return (
    <>
      <Hero tone="needsYou" testID="home-needs-you">
        <View style={styles.hero}>
          {detail !== null && (
            <Text style={styles.timer} testID="home-needs-you-timer">
              {elapsed(detail.raised_at, now)}
            </Text>
          )}
          <Dial
            size={170}
            rest={theme.colour.dialRestOnHero}
            segments={[{ fraction: 0.25, colour: theme.colour.needsYou }]}
          >
            <Disc icon="phone-ring" tone="needsYou" size={64} />
          </Dial>
          <Text style={styles.answer}>{t('home.answerYourPhone')}</Text>
          {detail !== null && (
            <>
              <Text style={styles.caller}>
                {detail.caller ?? detail.caller_label}
              </Text>
              <Text style={styles.why}>{detail.title}</Text>
            </>
          )}
        </View>
      </Hero>
      {detail?.body !== undefined && (
        <Text style={styles.note}>{detail.body}</Text>
      )}
      <Button
        label={t('home.seeWhole')}
        onPress={onOpen}
        testID="home-open-escalation"
      />
    </>
  );
}

const styles = StyleSheet.create({
  hero: {
    alignItems: 'center',
    gap: theme.space.sm,
    paddingVertical: theme.space.sm,
  },
  timer: {
    ...theme.type.strong,
    color: theme.colour.needsYouDeep,
    alignSelf: 'flex-end',
  },
  answer: { ...theme.type.heading, color: theme.colour.needsYouDeep },
  caller: { ...theme.type.strong, color: theme.colour.heroText },
  why: {
    ...theme.type.subtitle,
    color: theme.colour.heroMuted,
    textAlign: 'center',
  },
  note: { ...theme.type.subtitle, color: theme.colour.textMuted },
});
