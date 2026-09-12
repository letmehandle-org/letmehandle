import React from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { theme } from '../theme';

interface Props {
  readonly title?: string;
  readonly subtitle?: string;
  readonly children: React.ReactNode;
  /** Whether the body may be longer than the screen. */
  readonly scrollable?: boolean;
  readonly testID?: string;
}

/**
 * The shape every screen shares.
 *
 * Here rather than repeated: the safe area, the keyboard behaviour and the spacing are the
 * things that get slightly different on the fifth screen somebody writes.
 */
export function Screen({
  title,
  subtitle,
  children,
  scrollable = false,
  testID,
}: Props): React.JSX.Element {
  return (
    <SafeAreaView style={styles.safe} testID={testID}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.content}>
          {title !== undefined && (
            <Text accessibilityRole="header" style={styles.title}>
              {title}
            </Text>
          )}
          {subtitle !== undefined && (
            <Text style={styles.subtitle}>{subtitle}</Text>
          )}
          {scrollable ? (
            // A settings section asks more questions than fit on a phone, and content that
            // runs off the bottom of a fixed body is content nobody knows is there.
            <ScrollView
              style={styles.flex}
              contentContainerStyle={styles.scrollBody}
              keyboardShouldPersistTaps="handled"
            >
              {children}
            </ScrollView>
          ) : (
            <View style={styles.body}>{children}</View>
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colour.background },
  flex: { flex: 1 },
  content: { flex: 1, padding: theme.space.lg, gap: theme.space.sm },
  title: { ...theme.type.title, color: theme.colour.text },
  subtitle: { ...theme.type.body, color: theme.colour.textMuted },
  body: { flex: 1, gap: theme.space.md, paddingTop: theme.space.lg },
  // No flex here: a content container that fills the viewport cannot scroll past it.
  scrollBody: {
    gap: theme.space.md,
    paddingTop: theme.space.lg,
    paddingBottom: theme.space.xl,
  },
});
