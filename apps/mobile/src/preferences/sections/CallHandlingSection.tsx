import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import type {
  CallImportance,
  CallerCategory,
  HandlingPosture,
  Preferences,
} from '@letmehandle/api-client';

import { Choice, type Option } from '../../components/Choice';
import { theme } from '../../theme';
import { CATEGORIES, IMPORTANCE_LEVELS, POSTURES } from '../options';
import type { SectionProps } from './types';

/**
 * A category is either given its own posture or blocked, never both.
 *
 * The backend refuses a category that appears in both lists, and refusing it is right: the two
 * contradict each other. Offering one control per category with blocking as one of its answers
 * makes the contradiction unrepresentable rather than something to validate after the fact.
 */
type CategoryRule = HandlingPosture | 'default' | 'blocked';

/**
 * What happens to a call before anybody has spoken to it.
 *
 * The one section with no safe default, which is why it is the one step onboarding will not let
 * anybody skip.
 */
export function CallHandlingSection({
  preferences,
  onChange,
}: SectionProps): React.JSX.Element {
  const { t } = useTranslation();
  const handling = preferences.call_handling;

  const [defaultPosture, setDefaultPosture] = useState<HandlingPosture>(
    handling.default_posture,
  );
  const [anonymous, setAnonymous] = useState<HandlingPosture>(
    handling.anonymous_posture,
  );
  const [escalate, setEscalate] = useState<CallImportance>(
    handling.escalate_at_or_above,
  );
  const [rules, setRules] = useState<Record<string, CategoryRule>>(() =>
    rulesFrom(preferences),
  );

  useEffect(() => {
    const postures: Record<string, HandlingPosture> = {};
    const blocked: CallerCategory[] = [];
    for (const category of CATEGORIES) {
      const rule = rules[category];
      if (rule === 'blocked') {
        blocked.push(category);
      } else if (rule !== undefined && rule !== 'default') {
        postures[category] = rule;
      }
    }

    onChange({
      call_handling: {
        default_posture: defaultPosture,
        anonymous_posture: anonymous,
        escalate_at_or_above: escalate,
        posture_by_category: postures,
        blocked_categories: blocked,
      },
    });
  }, [defaultPosture, anonymous, escalate, rules, onChange]);

  const postureOptions: Option<HandlingPosture>[] = POSTURES.map(posture => ({
    value: posture,
    label: t(`preferences.posture.${posture}`),
  }));

  const categoryOptions: Option<CategoryRule>[] = [
    { value: 'default', label: t('preferences.call_handling.categoryDefault') },
    ...postureOptions,
    { value: 'blocked', label: t('preferences.call_handling.blocked') },
  ];

  return (
    <View style={styles.section}>
      <Choice
        label={t('preferences.call_handling.defaultPosture')}
        value={defaultPosture}
        options={postureOptions}
        onChange={setDefaultPosture}
        testID="handling-default"
      />
      <Choice
        label={t('preferences.call_handling.anonymousPosture')}
        value={anonymous}
        options={postureOptions}
        onChange={setAnonymous}
        testID="handling-anonymous"
      />
      <Choice
        label={t('preferences.call_handling.escalateAtOrAbove')}
        value={escalate}
        options={IMPORTANCE_LEVELS.map(level => ({
          value: level.value,
          label: t(`preferences.importance.${level.name}`),
        }))}
        onChange={setEscalate}
        testID="handling-escalate"
      />

      <Text accessibilityRole="header" style={styles.heading}>
        {t('preferences.call_handling.categories')}
      </Text>
      {CATEGORIES.map(category => (
        <Choice
          key={category}
          label={t(`preferences.category.${category}`)}
          value={rules[category] ?? 'default'}
          options={categoryOptions}
          onChange={rule => {
            setRules(current => ({ ...current, [category]: rule }));
          }}
          testID={`handling-category-${category}`}
        />
      ))}
    </View>
  );
}

function rulesFrom(preferences: Preferences): Record<string, CategoryRule> {
  const handling = preferences.call_handling;
  const blocked = new Set(handling.blocked_categories ?? []);
  const postures = handling.posture_by_category ?? {};

  const rules: Record<string, CategoryRule> = {};
  for (const category of CATEGORIES) {
    rules[category] = blocked.has(category)
      ? 'blocked'
      : postures[category] ?? 'default';
  }
  return rules;
}

const styles = StyleSheet.create({
  section: { gap: theme.space.lg },
  heading: { ...theme.type.body, fontWeight: '600', color: theme.colour.text },
});
