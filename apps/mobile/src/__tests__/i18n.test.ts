import { i18n, initialiseI18n } from '../i18n';
import { en } from '../i18n/locales/en';

describe('i18n', () => {
  beforeAll(async () => {
    await initialiseI18n('en');
  });

  it('resolves a known key', () => {
    expect(i18n.t('home.title')).toBe(en.home.title);
  });

  it('throws on a missing key rather than rendering it', () => {
    // A screen showing "home.nonexistent" to a user is a bug that reaches a release precisely
    // because it looks like a string. Failing here is how it is caught instead.
    expect(() => i18n.t('home.nonexistent')).toThrow(/missing translation/);
  });

  it('is configured with english as the fallback', () => {
    expect(i18n.options.fallbackLng).toEqual(['en']);
  });

  it('defaults to the configured locale when none is given', async () => {
    await initialiseI18n();
    expect(i18n.language).toBe('en');
  });
});
