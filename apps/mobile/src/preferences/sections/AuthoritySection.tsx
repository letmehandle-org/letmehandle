import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import type { Capability } from '@letmehandle/api-client';

import { Toggle } from '../../components/Toggle';
import { theme } from '../../theme';
import { CAPABILITIES } from '../options';
import type { SectionProps } from './types';

/**
 * What the assistant may do on somebody's behalf.
 *
 * Every capability is its own switch and every one starts off. A single "let it help" dial
 * would grant permissions nobody meant to give, and these are things said to strangers on the
 * telephone.
 */
export function AuthoritySection({
  preferences,
  onChange,
}: SectionProps): React.JSX.Element {
  const { t } = useTranslation();

  const [granted, setGranted] = useState<readonly Capability[]>(
    preferences.authority.capabilities ?? [],
  );

  useEffect(() => {
    onChange({ authority: { capabilities: [...granted] } });
  }, [granted, onChange]);

  return (
    <View style={styles.section}>
      {CAPABILITIES.map(capability => (
        <Toggle
          key={capability}
          label={t(`preferences.capability.${capability}`)}
          value={granted.includes(capability)}
          onChange={on => {
            setGranted(current =>
              on
                ? [...current, capability]
                : current.filter(entry => entry !== capability),
            );
          }}
          testID={`capability-${capability}`}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: theme.space.sm },
});
