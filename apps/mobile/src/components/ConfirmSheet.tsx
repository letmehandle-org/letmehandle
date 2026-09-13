import React from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { theme } from '../theme';
import { Button } from './Button';
import { Disc } from './Disc';
import { Icon, type IconName } from './icon/Icon';
import { Notice } from './Notice';

interface Props {
  readonly visible: boolean;
  readonly icon: IconName;
  readonly title: string;
  readonly body?: string;
  /** What goes, named one by one, so nobody deletes more than they meant to. */
  readonly items?: readonly string[];
  readonly confirm: string;
  readonly keep: string;
  readonly busy?: boolean;
  readonly problem?: string | null;
  readonly onConfirm: () => void;
  readonly onKeep: () => void;
  readonly testID?: string;
}

/**
 * Asking before something that cannot be undone.
 *
 * The confirming button is the only red in the system, and keeping is always one tap away:
 * the scrim, the second button and the system back all keep.
 */
export function ConfirmSheet({
  visible,
  icon,
  title,
  body,
  items = [],
  confirm,
  keep,
  busy = false,
  problem = null,
  onConfirm,
  onKeep,
  testID,
}: Props): React.JSX.Element {
  const { bottom } = useSafeAreaInsets();
  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onKeep}
    >
      <Pressable
        style={styles.scrim}
        onPress={onKeep}
        accessibilityRole="button"
        accessibilityLabel={keep}
      />
      <View
        style={[styles.sheet, { paddingBottom: bottom + theme.space.md }]}
        testID={testID}
      >
        <View style={styles.head}>
          <Disc icon={icon} tone="warning" size={56} />
          <Text accessibilityRole="header" style={styles.title}>
            {title}
          </Text>
          {body !== undefined && <Text style={styles.body}>{body}</Text>}
        </View>
        {items.map(item => (
          <View key={item} style={styles.item}>
            <Icon name="trash" size={18} colour={theme.colour.warning} />
            <Text style={styles.itemText}>{item}</Text>
          </View>
        ))}
        {problem !== null && <Notice tone="problem" message={problem} />}
        <Button
          label={confirm}
          variant="danger"
          busy={busy}
          onPress={onConfirm}
          testID={testID === undefined ? undefined : `${testID}-confirm`}
        />
        <Button
          label={keep}
          variant="quiet"
          onPress={onKeep}
          testID={testID === undefined ? undefined : `${testID}-keep`}
        />
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: { flex: 1, backgroundColor: 'rgba(29,20,54,0.32)' },
  sheet: {
    gap: theme.space.md,
    backgroundColor: theme.colour.surface,
    borderTopLeftRadius: theme.radius.lg,
    borderTopRightRadius: theme.radius.lg,
    paddingTop: theme.space.lg,
    paddingHorizontal: theme.space.gutter,
  },
  head: { alignItems: 'center', gap: theme.space.sm },
  title: {
    ...theme.type.heading,
    color: theme.colour.text,
    textAlign: 'center',
  },
  body: {
    ...theme.type.subtitle,
    color: theme.colour.textMuted,
    textAlign: 'center',
  },
  item: { flexDirection: 'row', alignItems: 'center', gap: theme.space.row },
  itemText: { ...theme.type.body, color: theme.colour.text },
});
