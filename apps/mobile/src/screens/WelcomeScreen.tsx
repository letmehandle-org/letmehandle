import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { BrandMark, Smile } from '../components/brand/BrandMark';
import { Button } from '../components/Button';
import { Disc, type Tone } from '../components/Disc';
import type { IconName } from '../components/icon/Icon';
import { Screen } from '../components/Screen';
import { theme } from '../theme';

interface Props {
  readonly onStart: () => void;
}

const PROMISES: readonly { key: string; icon: IconName; tone: Tone }[] = [
  { key: 'answers', icon: 'bot', tone: 'assistant' },
  { key: 'rings', icon: 'phone-ring', tone: 'needsYou' },
  { key: 'private', icon: 'lock', tone: 'quiet' },
];

/**
 * The first thing anybody sees: the promise, in the product's own name.
 *
 * "Let me handle it." is the headline, with "handle" in violet and the logo's apricot smile drawn
 * under it, so the mark and the sentence are read as one idea. Three plain promises follow and
 * nothing else competes: no illustration to decode, one button.
 */
export function WelcomeScreen({ onStart }: Props): React.JSX.Element {
  const { t } = useTranslation();
  // The smile is as wide as the word it underlines, measured once it is laid out.
  const [wordWidth, setWordWidth] = useState(0);

  return (
    <Screen
      scrollable
      testID="welcome-screen"
      footer={
        <>
          <Button
            label={t('welcome.start')}
            onPress={onStart}
            testID="start-button"
          />
          <Text style={styles.note}>{t('welcome.note')}</Text>
        </>
      }
    >
      <View style={styles.top}>
        <BrandMark size={52} />
      </View>

      <View
        accessible
        accessibilityRole="header"
        accessibilityLabel={`${t('welcome.title')} ${t(
          'welcome.titleAccent',
        )}${t('welcome.titleEnd')}`}
        style={styles.headline}
      >
        <Text style={styles.display}>{t('welcome.title')}</Text>
        <View>
          <Text style={styles.display}>
            <Text style={styles.accent}>{t('welcome.titleAccent')}</Text>
            {t('welcome.titleEnd')}
          </Text>
          {/* Laid out apart from the sentence, so it measures the word alone: absolute text is
              only as wide as what it says. Never seen. */}
          <Text
            style={[styles.display, styles.measure]}
            onLayout={event => {
              setWordWidth(Math.round(event.nativeEvent.layout.width));
            }}
          >
            {t('welcome.titleAccent')}
          </Text>
          {wordWidth > 0 && (
            <View style={styles.smile}>
              <Smile width={wordWidth} />
            </View>
          )}
        </View>
      </View>

      <Text style={styles.lead}>{t('welcome.lead')}</Text>

      <View style={styles.promises}>
        {PROMISES.map(promise => (
          <View key={promise.key} style={styles.promise}>
            <Disc icon={promise.icon} tone={promise.tone} size={44} />
            <Text style={styles.promiseText}>
              {t(`welcome.promises.${promise.key}`)}
            </Text>
          </View>
        ))}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { paddingTop: theme.space.lg },
  headline: { marginTop: theme.space.xl },
  display: { ...theme.type.display, color: theme.colour.text },
  accent: { color: theme.colour.accent },
  measure: { position: 'absolute', opacity: 0 },
  smile: { marginTop: -8, marginLeft: 2 },
  lead: {
    ...theme.type.body,
    color: theme.colour.textMuted,
    marginTop: theme.space.lg,
    maxWidth: 300,
  },
  promises: { gap: theme.space.md, marginTop: theme.space.xl },
  promise: { flexDirection: 'row', alignItems: 'center', gap: theme.space.row },
  promiseText: { ...theme.type.body, color: theme.colour.text, flexShrink: 1 },
  note: {
    ...theme.type.caption,
    color: theme.colour.textFaint,
    textAlign: 'center',
  },
});
