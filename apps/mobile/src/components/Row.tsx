import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { Disc, type Tone } from './Disc';
import { Icon, type IconName } from './icon/Icon';

interface Props {
  readonly title: string;
  readonly subtitle?: string;
  readonly icon?: IconName;
  readonly tone?: Tone;
  /** A short value shown on the right, such as "24/7" or "2 of 7". */
  readonly value?: string;
  /** Anything else on the right: a switch, a count. Replaces `value` and the chevron. */
  readonly trailing?: React.ReactNode;
  readonly onPress?: () => void;
  /** The last row in a card draws no hairline below itself. */
  readonly last?: boolean;
  readonly testID?: string;
}

/**
 * One line of a card: what it is on the left, where it stands on the right.
 *
 * Tappable rows show a chevron, and only tappable rows do. A chevron on something that does not
 * open is a promise the screen breaks.
 */
export function Row({
  title,
  subtitle,
  icon,
  tone = 'quiet',
  value,
  trailing,
  onPress,
  last = false,
  testID,
}: Props): React.JSX.Element {
  const content = (
    <>
      {icon !== undefined && <Disc icon={icon} tone={tone} size={40} />}
      <View style={styles.text}>
        <Text style={styles.title}>{title}</Text>
        {subtitle !== undefined && (
          <Text style={styles.subtitle}>{subtitle}</Text>
        )}
      </View>
      {trailing ?? (
        <View style={styles.end}>
          {value !== undefined && <Text style={styles.value}>{value}</Text>}
          {onPress !== undefined && (
            <Icon name="chev-r" colour={theme.colour.textGhost} size={18} />
          )}
        </View>
      )}
    </>
  );

  const rowStyle = [styles.row, !last && styles.hairline];

  if (onPress === undefined) {
    return (
      <View style={rowStyle} testID={testID}>
        {content}
      </View>
    );
  }

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={value === undefined ? title : `${title}, ${value}`}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [...rowStyle, pressed && styles.pressed]}
    >
      {content}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.row,
    minHeight: 64,
    paddingVertical: theme.space.row,
  },
  hairline: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colour.border,
  },
  pressed: { opacity: 0.7 },
  text: { flex: 1, gap: 2 },
  title: { ...theme.type.body, color: theme.colour.text },
  subtitle: { ...theme.type.caption, color: theme.colour.textFaint },
  end: { flexDirection: 'row', alignItems: 'center', gap: theme.space.xs },
  value: { ...theme.type.caption, color: theme.colour.textFaint },
});
