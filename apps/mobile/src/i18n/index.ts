/**
 * Translation, wired from the first screen so that no literal string ever reaches a component.
 *
 * One shipped locale today. The point of doing this now is that adding a second is translation
 * work rather than a change to every screen (D-017).
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import { environment } from '../config/environment';
import { en } from './locales/en';

export const DEFAULT_NAMESPACE = 'translation';

export async function initialiseI18n(
  locale: string = environment.defaultLocale,
): Promise<void> {
  await i18n.use(initReactI18next).init({
    resources: { en: { [DEFAULT_NAMESPACE]: en } },
    lng: locale,
    fallbackLng: 'en',
    defaultNS: DEFAULT_NAMESPACE,
    interpolation: { escapeValue: false },
    returnNull: false,
    // A missing key must be a test failure, not a screen showing "home.title" to a user.
    saveMissing: false,
    parseMissingKeyHandler: (key: string) => {
      throw new Error(`missing translation for "${key}"`);
    },
  });
}

export { i18n };
