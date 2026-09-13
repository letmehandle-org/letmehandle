import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text } from 'react-native';

import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { Field } from '../../components/Field';
import { Icon } from '../../components/icon/Icon';
import { Notice } from '../../components/Notice';
import { Row } from '../../components/Row';
import { Screen } from '../../components/Screen';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { factProblem, personalityWith } from '../../preferences/rules';
import { useImmediateSave } from '../../preferences/useImmediateSave';
import { theme } from '../../theme';

interface Props {
  readonly onBack: () => void;
}

/** What the assistant may volunteer about the user; nothing else is said. */
export function SayScreen({ onBack }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, busy, save } = useImmediateSave();
  const facts = preferences.personality.disclosable_facts ?? [];
  const [draft, setDraft] = useState('');
  const [draftProblem, setDraftProblem] = useState<string | null>(null);

  const add = (): void => {
    const wrong = factProblem(draft, facts);
    if (wrong !== null) {
      setDraftProblem(t(wrong));
      return;
    }
    save({
      personality: personalityWith(preferences, {
        disclosable_facts: [...facts, draft.trim()],
      }),
    });
    setDraft('');
    setDraftProblem(null);
  };

  return (
    <Screen
      onBack={onBack}
      title={t('say.title')}
      scrollable
      testID="settings-say"
    >
      <Notice message={t('say.never')} />

      <Text style={styles.label}>{t('say.allowed')}</Text>
      <Card>
        {facts.length === 0 ? (
          <Row icon="eye-off" title={t('say.empty')} last testID="say-empty" />
        ) : (
          facts.map((fact, position) => (
            <Row
              key={fact}
              icon="msg"
              tone="assistant"
              title={t('say.quoted', { fact })}
              last={position === facts.length - 1}
              trailing={
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={t('common.remove', { item: fact })}
                  hitSlop={10}
                  onPress={() => {
                    save({
                      personality: personalityWith(preferences, {
                        disclosable_facts: facts.filter(
                          entry => entry !== fact,
                        ),
                      }),
                    });
                  }}
                  testID={`say-remove-${position}`}
                >
                  <Icon name="x" colour={theme.colour.textFaint} size={20} />
                </Pressable>
              }
            />
          ))
        )}
      </Card>

      <Field
        label={t('say.add')}
        placeholder={t('say.placeholder')}
        value={draft}
        onChangeText={text => {
          setDraft(text);
          setDraftProblem(null);
        }}
        problem={draftProblem}
        returnKeyType="done"
        onSubmitEditing={add}
        testID="say-input"
      />
      <Button
        label={t('common.add')}
        variant="ghost"
        icon="plus"
        busy={busy}
        disabled={draft.trim() === ''}
        onPress={add}
        testID="say-add"
      />
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  label: {
    ...theme.type.label,
    color: theme.colour.textFaint,
    marginTop: theme.space.sm,
    marginLeft: theme.space.sm,
  },
});
