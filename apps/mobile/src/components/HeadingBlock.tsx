import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

/** A sub-page's own large title, below its round back button. */
export function HeadingBlock({
  title,
  subtitle,
}: {
  readonly title: string;
  readonly subtitle?: string;
}): React.JSX.Element {
  return (
    <View style={headingStyles.block}>
      <Text accessibilityRole="header" style={headingStyles.title}>
        {title}
      </Text>
      {subtitle !== undefined && (
        <Text style={headingStyles.subtitle}>{subtitle}</Text>
      )}
    </View>
  );
}

const headingStyles = StyleSheet.create({
  block: {
    gap: theme.space.sm,
    paddingTop: theme.space.lg,
    paddingBottom: theme.space.sm,
  },
  title: { ...theme.type.title, color: theme.colour.text },
  subtitle: { ...theme.type.subtitle, color: theme.colour.textMuted },
});
