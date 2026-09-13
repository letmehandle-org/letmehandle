import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '../components/Button';
import { Dial } from '../components/Dial';
import { Disc } from '../components/Disc';
import { Icon, type IconName } from '../components/icon/Icon';
import { Screen } from '../components/Screen';
import type { Tone } from '../components/Disc';
import { theme } from '../theme';

interface Props {
  readonly onStart: () => void;
}

/**
 * The first thing anybody sees, as a landing page in one screen.
 *
 * A phone being quietly looked after — a delivery settled, spam turned away, one call that needs
 * you — so the promise is seen before a word is read, then three pictures of what it does.
 */
export function WelcomeScreen({ onStart }: Props): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <Screen
      testID="welcome-screen"
      footer={
        <Button
          label={t('welcome.start')}
          onPress={onStart}
          testID="start-button"
        />
      }
    >
      <Illustration />

      <Text accessibilityRole="header" style={styles.title}>
        {t('welcome.title')}
        {'\n'}
        <Text style={styles.accent}>{t('welcome.titleAccent')}</Text>
      </Text>

      <View style={styles.features}>
        <Feature
          icon="bot"
          tone="assistant"
          label={t('welcome.features.answers')}
        />
        <Feature
          icon="phone-ring"
          tone="needsYou"
          label={t('welcome.features.rings')}
        />
        <Feature
          icon="lock"
          tone="quiet"
          label={t('welcome.features.private')}
        />
      </View>
    </Screen>
  );
}

function Feature({
  icon,
  tone,
  label,
}: {
  readonly icon: IconName;
  readonly tone: Tone;
  readonly label: string;
}): React.JSX.Element {
  return (
    <View style={styles.feature}>
      <Disc icon={icon} tone={tone} size={56} />
      <Text style={styles.featureText}>{label}</Text>
    </View>
  );
}

function Illustration(): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel={t('welcome.illustration.described')}
      style={styles.stage}
    >
      <View style={[styles.blob, styles.blobViolet]} />
      <View style={[styles.blob, styles.blobApricot]} />
      <View style={styles.sparkLeft}>
        <Icon name="sparkle" colour={theme.colour.accentSpark} size={22} />
      </View>
      <View style={styles.sparkRight}>
        <Icon name="sparkle" colour={theme.colour.needsYou} size={16} />
      </View>

      <View style={styles.phone}>
        <View style={styles.notch} />
        <Dial
          size={104}
          rest={theme.colour.well}
          segments={[
            { fraction: 0.62, colour: theme.colour.accent },
            { fraction: 0.08, colour: theme.colour.needsYou },
          ]}
        >
          <Text style={styles.phoneFigure}>39</Text>
        </Dial>
        <View style={styles.miniRows}>
          <MiniRow dot={theme.colour.accentWash} width={62} />
          <MiniRow dot={theme.colour.needsYouWash} width={48} />
          <MiniRow dot={theme.colour.quietWash} width={70} />
        </View>
      </View>

      <Chip
        style={styles.chipDelivery}
        icon="truck"
        tone="assistant"
        label={t('welcome.illustration.delivery')}
        tick
      />
      <Chip
        style={styles.chipNeedsYou}
        icon="phone-ring"
        tone="needsYou"
        label={t('welcome.illustration.needsYou')}
      />
      <Chip
        style={styles.chipSpam}
        icon="ban"
        tone="quiet"
        label={t('welcome.illustration.spam')}
      />
    </View>
  );
}

function MiniRow({
  dot,
  width,
}: {
  readonly dot: string;
  readonly width: number;
}): React.JSX.Element {
  return (
    <View style={styles.miniRow}>
      <View style={[styles.miniDot, { backgroundColor: dot }]} />
      <View style={[styles.miniLine, { width }]} />
    </View>
  );
}

function Chip({
  icon,
  tone,
  label,
  tick = false,
  style,
}: {
  readonly icon: IconName;
  readonly tone: Tone;
  readonly label: string;
  readonly tick?: boolean;
  readonly style: object;
}): React.JSX.Element {
  return (
    <View style={[styles.chip, style]}>
      <Disc icon={icon} tone={tone} size={32} />
      <Text style={styles.chipText}>{label}</Text>
      {tick && <Icon name="check" colour={theme.colour.accent} size={16} />}
    </View>
  );
}

const PHONE = { width: 150, height: 290 };

const styles = StyleSheet.create({
  stage: {
    height: 330,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: theme.space.sm,
  },
  blob: { position: 'absolute', borderRadius: 999 },
  blobViolet: {
    width: 260,
    height: 260,
    top: 20,
    backgroundColor: 'rgba(185,170,255,0.30)',
  },
  blobApricot: {
    width: 150,
    height: 150,
    right: 0,
    top: 150,
    backgroundColor: 'rgba(251,163,107,0.18)',
  },
  sparkLeft: { position: 'absolute', left: 30, top: 24 },
  sparkRight: { position: 'absolute', right: 44, top: 52 },
  phone: {
    width: PHONE.width,
    height: PHONE.height,
    borderRadius: 32,
    backgroundColor: theme.colour.surface,
    alignItems: 'center',
    paddingTop: 30,
    gap: theme.space.md,
    borderWidth: 6,
    borderColor: 'rgba(255,255,255,0.85)',
    ...theme.shadow.raised,
  },
  notch: {
    position: 'absolute',
    top: 10,
    width: 40,
    height: 6,
    borderRadius: 3,
    backgroundColor: theme.colour.border,
  },
  phoneFigure: {
    ...theme.type.heading,
    fontFamily: theme.font.light,
    color: theme.colour.text,
  },
  miniRows: { alignSelf: 'stretch', paddingHorizontal: 14, gap: 8 },
  miniRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: theme.colour.background,
    borderRadius: 10,
    padding: 7,
  },
  miniDot: { width: 16, height: 16, borderRadius: 8 },
  miniLine: {
    height: 6,
    borderRadius: 3,
    backgroundColor: theme.colour.border,
  },
  chip: {
    position: 'absolute',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingLeft: 5,
    paddingRight: 14,
    paddingVertical: 5,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colour.surface,
    ...theme.shadow.raised,
  },
  chipText: {
    ...theme.type.caption,
    fontFamily: theme.font.strong,
    color: theme.colour.text,
  },
  chipDelivery: { left: 0, top: 186, transform: [{ rotate: '-4deg' }] },
  chipNeedsYou: { right: 0, top: 70, transform: [{ rotate: '3deg' }] },
  chipSpam: { right: 10, top: 246, transform: [{ rotate: '2deg' }] },
  title: {
    ...theme.type.title,
    fontSize: 32,
    lineHeight: 40,
    color: theme.colour.text,
    textAlign: 'center',
  },
  accent: { color: theme.colour.accent },
  features: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: theme.space.sm,
  },
  feature: { flex: 1, alignItems: 'center', gap: theme.space.sm },
  featureText: {
    ...theme.type.caption,
    color: theme.colour.textMuted,
    textAlign: 'center',
  },
});
