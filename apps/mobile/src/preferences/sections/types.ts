/**
 * What every section editor looks like from outside.
 *
 * A section edits a draft and says what it would save; it does not save. Onboarding and
 * settings both wrap the same editors and differ only in what the button says and what happens
 * afterwards, which is only possible because the editors do not know which of the two they are
 * in.
 */
import type { Preferences, PreferencesUpdate } from '@letmehandle/api-client';

export interface SectionProps {
  readonly preferences: Preferences;
  /**
   * The change this draft would save, or null while it has a problem.
   *
   * Null rather than an error message, because the message belongs beside the field that is
   * wrong and the screen above only needs to know whether saving is possible.
   */
  readonly onChange: (changes: PreferencesUpdate | null) => void;
}
