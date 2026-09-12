/**
 * Which editor belongs to which section.
 *
 * A lookup rather than a switch, so that adding a section to `PREFERENCE_SECTIONS` without
 * writing its editor fails to compile instead of falling through to nothing at runtime.
 */
import React from 'react';

import type { PreferenceSection } from '../options';
import { AuthoritySection } from './AuthoritySection';
import { CallHandlingSection } from './CallHandlingSection';
import { HoursSection } from './HoursSection';
import { ImportantContactsSection } from './ImportantContactsSection';
import { NotificationsSection } from './NotificationsSection';
import { PersonalitySection } from './PersonalitySection';
import type { SectionProps } from './types';

const EDITORS: Record<
  PreferenceSection,
  (props: SectionProps) => React.JSX.Element
> = {
  call_handling: CallHandlingSection,
  important_contacts: ImportantContactsSection,
  hours: HoursSection,
  authority: AuthoritySection,
  notifications: NotificationsSection,
  personality: PersonalitySection,
};

export function SectionEditor({
  section,
  ...props
}: SectionProps & {
  readonly section: PreferenceSection;
}): React.JSX.Element {
  const Editor = EDITORS[section];
  return <Editor {...props} />;
}

export type { SectionProps };
