import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { theme } from '../theme';
import { Icon, type IconName } from './icon/Icon';

interface Action {
  readonly icon: IconName;
  readonly label: string;
  readonly onPress: () => void;
  readonly testID?: string;
}

interface Props {
  /**
   * A top-level screen's title, set large and to the left — or, with `onBack`, a sub-page's
   * title, set small and centred between a round back button and an optional action.
   */
  readonly title?: string;
  readonly subtitle?: string;
  readonly onBack?: () => void;
  readonly action?: Action;
  /** Drawn above the title, for onboarding's progress. */
  readonly header?: React.ReactNode;
  /** Pinned below the content: the screen's main button. */
  readonly footer?: React.ReactNode;
  readonly children: React.ReactNode;
  /** Whether the body may be longer than the screen. */
  readonly scrollable?: boolean;
  /** The safe area's bottom edge belongs to a tab bar below, not to this screen. */
  readonly insideTabs?: boolean;
  readonly testID?: string;
}

/**
 * The shape every screen shares.
 *
 * Here rather than repeated: the ground, the safe area, the keyboard, the gutter and the two
 * kinds of header are the things that get slightly different on the fifth screen somebody
 * writes.
 */
export function Screen({
  title,
  subtitle,
  onBack,
  action,
  header,
  footer,
  children,
  scrollable = false,
  insideTabs = false,
  testID,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const isPage = onBack !== undefined;

  return (
    <SafeAreaView
      style={styles.safe}
      edges={insideTabs ? ['top', 'left', 'right'] : undefined}
      testID={testID}
    >
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {isPage ? (
          <View style={styles.nav}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t('common.back')}
              onPress={onBack}
              hitSlop={8}
              style={({ pressed }) => [styles.round, pressed && styles.pressed]}
              testID={testID === undefined ? undefined : `${testID}-back`}
            >
              <Icon name="chev-l" colour={theme.colour.text} size={22} />
            </Pressable>
            {title !== undefined && (
              <Text
                accessibilityRole="header"
                numberOfLines={1}
                style={styles.navTitle}
              >
                {title}
              </Text>
            )}
            {action === undefined ? (
              <View style={styles.roundSpace} />
            ) : (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={action.label}
                onPress={action.onPress}
                hitSlop={8}
                style={({ pressed }) => [
                  styles.round,
                  pressed && styles.pressed,
                ]}
                testID={action.testID}
              >
                <Icon
                  name={action.icon}
                  colour={theme.colour.accentDeep}
                  size={20}
                />
              </Pressable>
            )}
          </View>
        ) : null}

        {scrollable ? (
          // Content that runs off the bottom of a fixed body is content nobody knows is there.
          <ScrollView
            style={styles.flex}
            contentContainerStyle={styles.scrollBody}
            keyboardShouldPersistTaps="handled"
          >
            <Heading
              header={header}
              title={isPage ? undefined : title}
              subtitle={subtitle}
            />
            {children}
          </ScrollView>
        ) : (
          <View style={styles.body}>
            <Heading
              header={header}
              title={isPage ? undefined : title}
              subtitle={subtitle}
            />
            {children}
          </View>
        )}

        {footer !== undefined && <View style={styles.footer}>{footer}</View>}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Heading({
  header,
  title,
  subtitle,
}: {
  readonly header?: React.ReactNode;
  readonly title?: string;
  readonly subtitle?: string;
}): React.JSX.Element | null {
  if (header === undefined && title === undefined && subtitle === undefined) {
    return null;
  }
  return (
    <View style={styles.heading}>
      {header}
      {title !== undefined && (
        <Text accessibilityRole="header" style={styles.title}>
          {title}
        </Text>
      )}
      {subtitle !== undefined && (
        <Text style={styles.subtitle}>{subtitle}</Text>
      )}
    </View>
  );
}

const ROUND = 44;

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colour.background },
  flex: { flex: 1 },
  nav: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: theme.space.gutter,
    paddingTop: theme.space.sm,
    paddingBottom: theme.space.sm,
    gap: theme.space.sm,
  },
  round: {
    width: ROUND,
    height: ROUND,
    borderRadius: ROUND / 2,
    backgroundColor: theme.colour.surface,
    alignItems: 'center',
    justifyContent: 'center',
    ...theme.shadow.card,
  },
  roundSpace: { width: ROUND, height: ROUND },
  pressed: { opacity: 0.8 },
  navTitle: {
    ...theme.type.strong,
    color: theme.colour.text,
    flexShrink: 1,
    textAlign: 'center',
  },
  heading: { gap: theme.space.sm, paddingTop: theme.space.md },
  title: { ...theme.type.title, color: theme.colour.text },
  subtitle: { ...theme.type.subtitle, color: theme.colour.textMuted },
  body: {
    flex: 1,
    gap: theme.space.md,
    paddingHorizontal: theme.space.gutter,
  },
  // No flex here: a content container that fills the viewport cannot scroll past it.
  scrollBody: {
    gap: theme.space.md,
    paddingHorizontal: theme.space.gutter,
    paddingBottom: theme.space.xl,
  },
  footer: {
    gap: theme.space.sm,
    paddingHorizontal: theme.space.gutter,
    paddingTop: theme.space.sm,
    paddingBottom: theme.space.md,
  },
});
