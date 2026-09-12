import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import type { TimeWindow } from '@letmehandle/api-client';

import { Field } from '../../components/Field';
import { Toggle } from '../../components/Toggle';
import { theme } from '../../theme';
import { windowProblem } from '../validation';
import type { SectionProps } from './types';

/** A window while it is being edited, which is a window that may not be valid yet. */
interface WindowDraft {
  readonly on: boolean;
  readonly start: string;
  readonly end: string;
  readonly zone: string;
}

/**
 * When somebody is working, and when they would rather not be disturbed.
 *
 * Either window may be off entirely, which is different from a window covering no time: off
 * means the rule does not apply, and the backend stores that as nothing at all.
 */
export function HoursSection({
  preferences,
  onChange,
}: SectionProps): React.JSX.Element {
  const { t } = useTranslation();

  const [working, setWorking] = useState<WindowDraft>(() =>
    draftFrom(preferences.hours.working ?? null, '09:00', '17:30'),
  );
  const [quiet, setQuiet] = useState<WindowDraft>(() =>
    draftFrom(preferences.hours.quiet ?? null, '22:00', '07:00'),
  );

  const workingProblem = working.on ? windowProblem(windowOf(working)) : null;
  const quietProblem = quiet.on ? windowProblem(windowOf(quiet)) : null;

  useEffect(() => {
    if (workingProblem !== null || quietProblem !== null) {
      onChange(null);
      return;
    }
    onChange({
      hours: {
        working: working.on ? windowOf(working) : null,
        quiet: quiet.on ? windowOf(quiet) : null,
      },
    });
  }, [working, quiet, workingProblem, quietProblem, onChange]);

  return (
    <View style={styles.section}>
      <WindowEditor
        title={t('preferences.hours.working')}
        draft={working}
        problem={workingProblem === null ? null : t(workingProblem)}
        onChange={setWorking}
        testID="hours-working"
      />
      <WindowEditor
        title={t('preferences.hours.quiet')}
        draft={quiet}
        problem={quietProblem === null ? null : t(quietProblem)}
        onChange={setQuiet}
        testID="hours-quiet"
      />
    </View>
  );
}

function WindowEditor({
  title,
  draft,
  problem,
  onChange,
  testID,
}: {
  readonly title: string;
  readonly draft: WindowDraft;
  readonly problem: string | null;
  readonly onChange: (draft: WindowDraft) => void;
  readonly testID: string;
}): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <View style={styles.window}>
      <Toggle
        label={title}
        value={draft.on}
        onChange={on => {
          onChange({ ...draft, on });
        }}
        testID={`${testID}-on`}
      />
      {draft.on && (
        <>
          <Field
            label={t('preferences.hours.start')}
            value={draft.start}
            onChangeText={start => {
              onChange({ ...draft, start });
            }}
            keyboardType="numbers-and-punctuation"
            testID={`${testID}-start`}
          />
          <Field
            label={t('preferences.hours.end')}
            value={draft.end}
            onChangeText={end => {
              onChange({ ...draft, end });
            }}
            keyboardType="numbers-and-punctuation"
            // The problem sits on the last field of the pair rather than on both, so a single
            // mistake is reported once.
            problem={problem}
            testID={`${testID}-end`}
          />
          <Field
            label={t('preferences.hours.zone')}
            value={draft.zone}
            onChangeText={zone => {
              onChange({ ...draft, zone });
            }}
            autoCapitalize="none"
            autoCorrect={false}
            testID={`${testID}-zone`}
          />
        </>
      )}
    </View>
  );
}

function windowOf(draft: WindowDraft): TimeWindow {
  return { start: draft.start, end: draft.end, zone: draft.zone };
}

function draftFrom(
  window: TimeWindow | null,
  start: string,
  end: string,
): WindowDraft {
  if (window !== null) {
    return {
      on: true,
      start: window.start,
      end: window.end,
      zone: window.zone,
    };
  }
  // Suggested rather than stored: a window that has never been set still needs something in
  // the fields the moment somebody switches it on, and an empty one reads as a broken form.
  return { on: false, start, end, zone: deviceZone() };
}

/**
 * The timezone the phone is set to.
 *
 * The window is compared in its own zone on the server, so guessing wrong means calls handled
 * at the wrong time of day. The device's own setting is the best guess available, and the field
 * stays editable for anybody it is wrong for.
 */
function deviceZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

const styles = StyleSheet.create({
  section: { gap: theme.space.lg },
  window: { gap: theme.space.sm },
});
