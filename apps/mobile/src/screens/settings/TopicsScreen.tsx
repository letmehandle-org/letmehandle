import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { Notice } from '../../components/Notice';
import { Screen } from '../../components/Screen';
import { Tag } from '../../components/Tag';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { personalityWith } from '../../preferences/rules';
import { useImmediateSave } from '../../preferences/useImmediateSave';
import { normaliseTopic, topicProblem } from '../../preferences/validation';
import { theme } from '../../theme';

interface Props {
  readonly onBack: () => void;
}

/** The most topics the API holds. */
const MAX_TOPICS = 50;

/** Short topics the assistant listens for, as chips. */
export function TopicsScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, busy, save } = useImmediateSave();
  const topics = preferences.personality.topics ?? [];
  const [draft, setDraft] = useState('');
  const [draftProblem, setDraftProblem] = useState<string | null>(null);

  const add = (): void => {
    const wrong = topicProblem(draft, topics);
    if (wrong !== null) {
      setDraftProblem(t(wrong));
      return;
    }
    save({
      personality: personalityWith(preferences, {
        topics: [...topics, normaliseTopic(draft)],
      }),
    });
    setDraft('');
    setDraftProblem(null);
  };

  return (
    <Screen
      onBack={onBack}
      title={t('topics.title')}
      scrollable
      testID="settings-topics"
    >
      {topics.length === 0 ? (
        <Text style={styles.empty} testID="topics-empty">
          {t('topics.empty')}
        </Text>
      ) : (
        <View style={styles.tags}>
          {topics.map(topic => (
            <Tag
              key={topic}
              label={topic}
              removeLabel={t('common.remove', { item: topic })}
              onRemove={() => {
                save({
                  personality: personalityWith(preferences, {
                    topics: topics.filter(entry => entry !== topic),
                  }),
                });
              }}
              testID={`topic-remove-${topic}`}
            />
          ))}
        </View>
      )}

      <Field
        label={t('topics.add')}
        placeholder={t('topics.placeholder')}
        value={draft}
        onChangeText={text => {
          setDraft(text);
          setDraftProblem(null);
        }}
        problem={draftProblem}
        autoCapitalize="none"
        returnKeyType="done"
        onSubmitEditing={add}
        testID="topic-input"
      />
      <Button
        label={t('common.add')}
        variant="ghost"
        icon="plus"
        busy={busy}
        disabled={draft.trim() === ''}
        onPress={add}
        testID="topic-add"
      />
      <Text style={styles.count}>
        {t('topics.count', { count: topics.length, max: MAX_TOPICS })}
      </Text>
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  tags: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: theme.space.sm,
    paddingTop: theme.space.sm,
  },
  empty: {
    ...theme.type.body,
    color: theme.colour.textMuted,
    paddingTop: theme.space.sm,
  },
  count: {
    ...theme.type.caption,
    color: theme.colour.textFaint,
    textAlign: 'center',
  },
});
