import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { Hero } from './Hero';
import { Icon, type IconName } from './icon/Icon';

interface LaneProps {
  readonly from: { readonly icon: IconName; readonly label: string };
  readonly to: { readonly icon: IconName; readonly label: string };
  readonly assistant?: boolean;
  readonly testID?: string;
}

function Ends({ from, to, assistant = false }: LaneProps): React.JSX.Element {
  return (
    <View style={styles.row}>
      <End
        icon={from.icon}
        label={from.label}
        tone={assistant ? 'soft' : 'quiet'}
      />
      <Icon
        name="arrow-r"
        colour={assistant ? theme.colour.heroMuted : theme.colour.textGhost}
        size={20}
      />
      <End
        icon={to.icon}
        label={to.label}
        tone={assistant ? 'filled' : 'quiet'}
      />
    </View>
  );
}

function End({
  icon,
  label,
  tone,
}: {
  readonly icon: IconName;
  readonly label: string;
  readonly tone: 'quiet' | 'soft' | 'filled';
}): React.JSX.Element {
  return (
    <View style={styles.end}>
      <View style={[styles.circle, styles[tone]]}>
        <Icon
          name={icon}
          size={26}
          colour={
            tone === 'filled'
              ? theme.colour.onAccent
              : tone === 'soft'
              ? theme.colour.heroMuted
              : theme.colour.textMuted
          }
        />
      </View>
      <Text style={[styles.label, tone !== 'quiet' && styles.heroLabel]}>
        {label}
      </Text>
    </View>
  );
}

/** Where a kind of call goes, read left to right; the assistant's lane sits on the pastel ground. */
export function Lane(props: LaneProps): React.JSX.Element {
  const { t } = useTranslation();
  const described = t('common.labelled', {
    label: props.from.label,
    value: props.to.label,
  });
  if (props.assistant === true) {
    return (
      <Hero style={styles.laneHero} testID={props.testID}>
        <View accessible accessibilityLabel={described} style={styles.fill}>
          <Ends {...props} />
        </View>
      </Hero>
    );
  }
  return (
    <View
      accessible
      accessibilityLabel={described}
      style={styles.lane}
      testID={props.testID}
    >
      <Ends {...props} />
    </View>
  );
}

const CIRCLE = 64;

const styles = StyleSheet.create({
  lane: {
    backgroundColor: theme.colour.surface,
    borderRadius: theme.radius.lg,
    paddingVertical: theme.space.lg,
    paddingHorizontal: theme.space.md,
    ...theme.shadow.card,
  },
  laneHero: { paddingVertical: theme.space.lg, alignItems: 'stretch' },
  fill: { alignSelf: 'stretch' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
  },
  end: { flex: 1, alignItems: 'center', gap: theme.space.sm },
  circle: {
    width: CIRCLE,
    height: CIRCLE,
    borderRadius: CIRCLE / 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  quiet: { backgroundColor: theme.colour.quietWash },
  soft: { backgroundColor: 'rgba(255,255,255,0.75)' },
  filled: { backgroundColor: theme.colour.accent, ...theme.shadow.accent },
  label: {
    ...theme.type.subtitle,
    fontFamily: theme.font.strong,
    color: theme.colour.text,
    textAlign: 'center',
  },
  heroLabel: { color: theme.colour.heroText },
});
