import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { COUNTRIES, flagOf, type CountryCode } from '../auth/countries';
import { theme } from '../theme';
import { Icon } from './icon/Icon';

interface Props {
  readonly visible: boolean;
  readonly selected: CountryCode;
  readonly onPick: (code: CountryCode) => void;
  readonly onClose: () => void;
}

/** Every country a number can be entered for: a flag, a name and a calling code, one tap each. */
export function CountrySheet({
  visible,
  selected,
  onPick,
  onClose,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { bottom } = useSafeAreaInsets();
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
        accessibilityLabel={t('phone.chooseCountry')}
        testID="country-sheet-close"
      />
      <View
        style={[styles.sheet, { paddingBottom: bottom + theme.space.md }]}
        testID="country-sheet"
      >
        <Text accessibilityRole="header" style={styles.title}>
          {t('phone.chooseCountry')}
        </Text>
        <FlatList
          data={COUNTRIES}
          keyExtractor={country => country.code}
          renderItem={({ item }) => {
            const chosen = item.code === selected;
            return (
              <Pressable
                accessibilityRole="radio"
                accessibilityState={{ checked: chosen }}
                accessibilityLabel={t('phone.countryValue', {
                  country: t(`phone.countries.${item.code}`),
                  dial: item.dial,
                })}
                onPress={() => {
                  onPick(item.code);
                }}
                style={[styles.row, chosen && styles.rowOn]}
                testID={`country-${item.code}`}
              >
                <Text style={styles.flag}>{flagOf(item.code)}</Text>
                <Text style={[styles.name, chosen && styles.nameOn]}>
                  {t(`phone.countries.${item.code}`)}
                </Text>
                <Text style={styles.dial}>+{item.dial}</Text>
                {chosen && (
                  <Icon
                    name="check"
                    size={18}
                    colour={theme.colour.accentDeep}
                  />
                )}
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
    maxHeight: '70%',
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
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.row,
    minHeight: 56,
    paddingHorizontal: theme.space.md,
    borderRadius: theme.radius.sm,
  },
  rowOn: { backgroundColor: theme.colour.accentWash },
  flag: { fontSize: 24 },
  name: { ...theme.type.body, color: theme.colour.text, flex: 1 },
  nameOn: { fontFamily: theme.font.strong, color: theme.colour.accentDeep },
  dial: {
    ...theme.type.body,
    color: theme.colour.textMuted,
    fontVariant: ['tabular-nums'],
  },
});
