import React from 'react';
import {
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { TIMES } from '../preferences/hours';
import { theme } from '../theme';

interface Props {
  readonly title: string;
  readonly visible: boolean;
  readonly selected: string;
  /** Times that may not be picked, such as the other end of the window. */
  readonly unavailable?: readonly string[];
  readonly onPick: (time: string) => void;
  readonly onClose: () => void;
  readonly testID?: string;
}

const ROW = 52;

/**
 * A sheet of times, every half hour, opening at the one already chosen.
 *
 * A list rather than a spinning wheel: nothing to install, every option readable at once, and a
 * screen reader reads it as the list it is.
 */
export function TimeSheet({
  title,
  visible,
  selected,
  unavailable = [],
  onPick,
  onClose,
  testID,
}: Props): React.JSX.Element {
  const { bottom } = useSafeAreaInsets();
  const index = Math.max(0, TIMES.indexOf(selected));

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <Pressable
        style={styles.scrim}
        onPress={onClose}
        accessibilityRole="button"
        accessibilityLabel={title}
        testID={testID === undefined ? undefined : `${testID}-close`}
      />
      <View
        style={[styles.sheet, { paddingBottom: bottom + theme.space.md }]}
        testID={testID}
      >
        <Text accessibilityRole="header" style={styles.title}>
          {title}
        </Text>
        <FlatList
          data={TIMES}
          keyExtractor={time => time}
          initialScrollIndex={Math.max(0, index - 2)}
          getItemLayout={(_, position) => ({
            length: ROW,
            offset: ROW * position,
            index: position,
          })}
          renderItem={({ item }) => {
            const chosen = item === selected;
            const disabled = unavailable.includes(item);
            return (
              <Pressable
                accessibilityRole="radio"
                accessibilityState={{ checked: chosen, disabled }}
                disabled={disabled}
                onPress={() => {
                  onPick(item);
                }}
                style={[styles.row, chosen && styles.rowOn]}
                testID={testID === undefined ? undefined : `${testID}-${item}`}
              >
                <Text
                  style={[
                    styles.time,
                    chosen && styles.timeOn,
                    disabled && styles.timeOff,
                  ]}
                >
                  {item}
                </Text>
              </Pressable>
            );
          }}
        />
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: { flex: 1, backgroundColor: 'rgba(29,20,54,0.32)' },
  sheet: {
    maxHeight: '55%',
    backgroundColor: theme.colour.surface,
    borderTopLeftRadius: theme.radius.lg,
    borderTopRightRadius: theme.radius.lg,
    paddingTop: theme.space.lg,
    paddingHorizontal: theme.space.gutter,
  },
  title: {
    ...theme.type.strong,
    color: theme.colour.text,
    textAlign: 'center',
    marginBottom: theme.space.sm,
  },
  row: {
    height: ROW,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: theme.radius.sm,
  },
  rowOn: { backgroundColor: theme.colour.accentWash },
  time: { ...theme.type.body, color: theme.colour.text },
  timeOn: { fontFamily: theme.font.strong, color: theme.colour.accentDeep },
  timeOff: { color: theme.colour.textGhost },
});
