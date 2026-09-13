/** Translation for every string in the app (D-017). */
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
    // A missing key throws instead of rendering the key.
    saveMissing: false,
    parseMissingKeyHandler: (key: string) => {
      throw new Error(`missing translation for "${key}"`);
    },
  });
}

export { i18n };
