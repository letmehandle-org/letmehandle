import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type {
  HandlingPosture,
  ImportantContact,
} from '@letmehandle/api-client';

import { Button } from '../../components/Button';
import { Choice, type Option } from '../../components/Choice';
import { Field } from '../../components/Field';
import { Notice } from '../../components/Notice';
import { theme } from '../../theme';
import { POSTURES } from '../options';
import { contactProblem, normaliseNumber } from '../validation';
import type { SectionProps } from './types';

/**
 * People whose calls are treated as the user says rather than as the assistant judges.
 *
 * The list is edited in place and saved whole. The backend replaces the list with what it is
 * sent, so a screen that sent only the entry just added would delete the rest.
 */
export function ImportantContactsSection({
  preferences,
  onChange,
}: SectionProps): React.JSX.Element {
  const { t } = useTranslation();

  const [contacts, setContacts] = useState<readonly ImportantContact[]>(
    preferences.important_contacts,
  );
  const [label, setLabel] = useState('');
  const [number, setNumber] = useState('');
  const [posture, setPosture] = useState<HandlingPosture>('pass_through');
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    onChange({ important_contacts: [...contacts] });
  }, [contacts, onChange]);

  const add = (): void => {
    const draft: ImportantContact = {
      label: label.trim(),
      phone_number: normaliseNumber(number),
      posture,
    };

    const wrong = contactProblem(draft, contacts);
    if (wrong !== null) {
      setProblem(t(wrong));
      return;
    }

    setContacts(current => [...current, draft]);
    setLabel('');
    setNumber('');
    setProblem(null);
  };

  return (
    <View style={styles.section}>
      {contacts.length === 0 ? (
        <Notice
          message={t('preferences.important_contacts.empty')}
          testID="contacts-empty"
        />
      ) : (
        contacts.map(contact => (
          <View key={contact.phone_number} style={styles.entry}>
            <View style={styles.entryText}>
              <Text style={styles.entryLabel}>{contact.label}</Text>
              <Text style={styles.entryDetail}>
                {contact.phone_number} ·{' '}
                {t(`preferences.posture.${contact.posture}`)}
              </Text>
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`${t('common.remove')} ${contact.label}`}
              testID={`contact-remove-${contact.phone_number}`}
              onPress={() => {
                setContacts(current =>
                  current.filter(
                    entry => entry.phone_number !== contact.phone_number,
                  ),
                );
              }}
            >
              <Text style={styles.remove}>{t('common.remove')}</Text>
            </Pressable>
          </View>
        ))
      )}

      <Field
        label={t('preferences.important_contacts.label')}
        placeholder={t('preferences.important_contacts.labelPlaceholder')}
        value={label}
        onChangeText={setLabel}
        testID="contact-label"
      />
      <Field
        label={t('preferences.important_contacts.number')}
        placeholder={t('preferences.important_contacts.numberPlaceholder')}
        value={number}
        onChangeText={setNumber}
        keyboardType="phone-pad"
        autoComplete="tel"
        testID="contact-number"
      />
      <Choice
        label={t('preferences.important_contacts.posture')}
        value={posture}
        options={POSTURES.map<Option<HandlingPosture>>(value => ({
          value,
          label: t(`preferences.posture.${value}`),
        }))}
        onChange={setPosture}
        testID="contact-posture"
      />
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="contact-problem" />
      )}
      <Button
        label={t('common.add')}
        variant="quiet"
        onPress={add}
        testID="contact-add"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: theme.space.md },
  entry: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: theme.space.md,
    backgroundColor: theme.colour.surface,
    borderRadius: theme.radius.sm,
    padding: theme.space.md,
  },
  entryText: { gap: theme.space.xs, flexShrink: 1 },
  entryLabel: { ...theme.type.body, color: theme.colour.text },
  entryDetail: {
    ...theme.type.body,
    fontSize: 14,
    color: theme.colour.textMuted,
  },
  remove: { ...theme.type.body, fontSize: 14, color: theme.colour.warning },
});
