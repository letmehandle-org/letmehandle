import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { Formality, Verbosity } from '@letmehandle/api-client';

import { Button } from '../../components/Button';
import { Choice } from '../../components/Choice';
import { Field } from '../../components/Field';
import { Notice } from '../../components/Notice';
import { theme } from '../../theme';
import { FORMALITIES, VERBOSITIES } from '../options';
import { normaliseTopic, topicProblem } from '../validation';
import type { SectionProps } from './types';

/**
 * How the assistant sounds, and the handful of things worth fetching somebody for.
 *
 * Tone and length are separate questions because they vary independently: a warm assistant can
 * be brief and a formal one can go on, and one dial would make half the combinations people
 * want unreachable.
 */
export function PersonalitySection({
  preferences,
  onChange,
}: SectionProps): React.JSX.Element {
  const { t } = useTranslation();
  const personality = preferences.personality;

  const [formality, setFormality] = useState<Formality>(personality.formality);
  const [verbosity, setVerbosity] = useState<Verbosity>(personality.verbosity);
  const [topics, setTopics] = useState<readonly string[]>(
    personality.topics ?? [],
  );
  const [draft, setDraft] = useState('');
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    onChange({
      personality: { formality, verbosity, topics: [...topics] },
    });
  }, [formality, verbosity, topics, onChange]);

  const add = (): void => {
    const wrong = topicProblem(draft, topics);
    if (wrong !== null) {
      setProblem(t(wrong));
      return;
    }
    setTopics(current => [...current, normaliseTopic(draft)]);
    setDraft('');
    setProblem(null);
  };

  return (
    <View style={styles.section}>
      <Choice
        label={t('preferences.personality.formality')}
        value={formality}
        options={FORMALITIES.map(value => ({
          value,
          label: t(`preferences.formality.${value}`),
        }))}
        onChange={setFormality}
        testID="personality-formality"
      />
      <Choice
        label={t('preferences.personality.verbosity')}
        value={verbosity}
        options={VERBOSITIES.map(value => ({
          value,
          label: t(`preferences.verbosity.${value}`),
        }))}
        onChange={setVerbosity}
        testID="personality-verbosity"
      />

      <Text accessibilityRole="header" style={styles.heading}>
        {t('preferences.personality.topics')}
      </Text>
      {topics.length === 0 ? (
        <Notice
          message={t('preferences.personality.topicsEmpty')}
          testID="topics-empty"
        />
      ) : (
        <View style={styles.topics}>
          {topics.map(topic => (
            <Pressable
              key={topic}
              accessibilityRole="button"
              accessibilityLabel={`${t('common.remove')} ${topic}`}
              testID={`topic-remove-${topic}`}
              onPress={() => {
                setTopics(current => current.filter(entry => entry !== topic));
              }}
              style={styles.topic}
            >
              <Text style={styles.topicText}>{topic}</Text>
            </Pressable>
          ))}
        </View>
      )}

      <Field
        label={t('preferences.personality.topics')}
        placeholder={t('preferences.personality.topicPlaceholder')}
        value={draft}
        onChangeText={setDraft}
        autoCapitalize="none"
        testID="topic-input"
      />
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="topic-problem" />
      )}
      <Button
        label={t('common.add')}
        variant="quiet"
        onPress={add}
        testID="topic-add"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: theme.space.md },
  heading: { ...theme.type.body, fontWeight: '600', color: theme.colour.text },
  topics: { flexDirection: 'row', flexWrap: 'wrap', gap: theme.space.sm },
  topic: {
    minHeight: 40,
    justifyContent: 'center',
    paddingHorizontal: theme.space.md,
    borderRadius: theme.radius.sm,
    backgroundColor: theme.colour.surface,
  },
  topicText: { ...theme.type.body, fontSize: 14, color: theme.colour.text },
});
