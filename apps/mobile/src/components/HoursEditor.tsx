import type { TimeWindow } from '@letmehandle/api-client';
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import {
  firstWindow,
  hoursFigure,
  onTheRing,
  withEnd,
} from '../preferences/hours';
import { theme } from '../theme';
import { Dial, DialCaption, DialFigure } from './Dial';
import { Disc } from './Disc';
import { Hero } from './Hero';
import { Icon } from './icon/Icon';
import { Segmented } from './Segmented';
import { TimeSheet } from './TimeSheet';

interface Props {
  /** The window the assistant answers in, or null for around the clock. */
  readonly value: TimeWindow | null;
  readonly onChange: (value: TimeWindow | null) => void;
}

type End = 'start' | 'end';

/**
 * When the assistant answers, as one ring on a clock (D-030).
 *
 * Around the clock is a full ring and nothing else to decide. Set hours draw the window where it
 * falls in the day, with the two times as the only inputs and one line saying what happens
 * outside them. Shared by setup, which saves on Next, and settings, which saves on every change.
 */
export function HoursEditor({ value, onChange }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const [choosing, setChoosing] = useState<End | null>(null);

  return (
    <View style={styles.editor}>
      {value === null ? <AroundTheClock /> : <Window window={value} />}

      <Segmented
        label={t('setup.hours.title')}
        value={value === null ? 'always' : 'set'}
        options={[
          { value: 'always', label: t('hours.allTheTime') },
          { value: 'set', label: t('hours.set') },
        ]}
        onChange={mode => {
          onChange(mode === 'always' ? null : firstWindow());
        }}
        testID="hours-mode"
      />

      {value !== null && (
        <>
          <View style={styles.times}>
            <TimeCard
              label={t('hours.from')}
              time={value.start}
              onPress={() => {
                setChoosing('start');
              }}
              testID="hours-from"
            />
            <TimeCard
              label={t('hours.to')}
              time={value.end}
              onPress={() => {
                setChoosing('end');
              }}
              testID="hours-to"
            />
          </View>
          <View style={styles.outside}>
            <Disc icon="phone" tone="quiet" size={36} />
            <Text style={styles.outsideText}>{t('hours.outside')}</Text>
          </View>
          <TimeSheet
            title={t(choosing === 'end' ? 'hours.to' : 'hours.from')}
            visible={choosing !== null}
            selected={choosing === 'end' ? value.end : value.start}
            unavailable={[choosing === 'end' ? value.start : value.end]}
            onPick={time => {
              const next = withEnd(value, choosing ?? 'start', time);
              setChoosing(null);
              if (next !== null) {
                onChange(next);
              }
            }}
            onClose={() => {
              setChoosing(null);
            }}
            testID="hours-sheet"
          />
        </>
      )}
    </View>
  );
}

/** The full ring that means the assistant answers at any hour. */
export function AroundTheClock(): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <Hero testID="hours-always">
      <Dial
        size={210}
        rest={theme.colour.dialRestOnHero}
        segments={[{ fraction: 1, colour: theme.colour.accent }]}
      >
        <Icon name="infinity" colour={theme.colour.accentDeep} size={26} />
        <DialFigure text={t('hours.always')} />
        <DialCaption text={t('hours.alwaysOn')} />
      </Dial>
    </Hero>
  );
}

function Window({
  window,
}: {
  readonly window: TimeWindow;
}): React.JSX.Element {
  const { t } = useTranslation();
  const ring = onTheRing(window);
  return (
    <Hero testID="hours-window">
      <Dial
        size={210}
        rest={theme.colour.dialRestOnHero}
        start={ring.start}
        segments={[{ fraction: ring.fraction, colour: theme.colour.accent }]}
      >
        <Icon name="bot" colour={theme.colour.accentDeep} size={24} />
        <DialFigure
          text={t('hours.figure', hoursFigure(window))}
          testID="hours-figure"
        />
        <DialCaption text={t('hours.aDay')} />
      </Dial>
    </Hero>
  );
}

function TimeCard({
  label,
  time,
  onPress,
  testID,
}: {
  readonly label: string;
  readonly time: string;
  readonly onPress: () => void;
  readonly testID: string;
}): React.JSX.Element {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${label}, ${time}`}
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
      testID={testID}
    >
      <Text style={styles.cardLabel}>{label}</Text>
      <Text style={styles.cardTime}>{time}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  editor: { gap: theme.space.md },
  times: { flexDirection: 'row', gap: theme.space.row },
  card: {
    flex: 1,
    gap: theme.space.xs,
    paddingVertical: theme.space.md,
    paddingHorizontal: theme.space.md,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colour.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.colour.border,
    ...theme.shadow.card,
  },
  pressed: { opacity: 0.8 },
  cardLabel: { ...theme.type.label, color: theme.colour.textFaint },
  cardTime: { ...theme.type.heading, color: theme.colour.text },
  outside: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.row,
  },
  outsideText: {
    ...theme.type.subtitle,
    color: theme.colour.textMuted,
    flexShrink: 1,
  },
});
