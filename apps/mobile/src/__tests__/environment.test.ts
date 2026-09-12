import { ConfigurationError, readEnvironment } from '../config/environment';

const valid = {
  APP_ENV: 'development',
  API_BASE_URL: 'http://localhost:8000',
  DEFAULT_LOCALE: 'en',
};

describe('environment', () => {
  it('reads a valid configuration', () => {
    expect(readEnvironment(valid)).toEqual({
      name: 'development',
      apiBaseUrl: 'http://localhost:8000',
      defaultLocale: 'en',
    });
  });

  it('defaults the environment name when it is not set', () => {
    expect(readEnvironment({ ...valid, APP_ENV: undefined }).name).toBe(
      'development',
    );
  });

  it.each(['staging', 'production'])('accepts %s', name => {
    expect(readEnvironment({ ...valid, APP_ENV: name }).name).toBe(name);
  });

  it('rejects an unknown environment name', () => {
    expect(() => readEnvironment({ ...valid, APP_ENV: 'wherever' })).toThrow(
      ConfigurationError,
    );
  });

  it('names the variable that is missing', () => {
    expect(() =>
      readEnvironment({ ...valid, API_BASE_URL: undefined }),
    ).toThrow(/API_BASE_URL/);
  });

  it('rejects a blank value as firmly as a missing one', () => {
    expect(() => readEnvironment({ ...valid, API_BASE_URL: '   ' })).toThrow(
      /API_BASE_URL/,
    );
  });

  it('rejects a base URL that is not http or https', () => {
    expect(() =>
      readEnvironment({ ...valid, API_BASE_URL: 'localhost:8000' }),
    ).toThrow(/http or https/);
  });

  it('strips a trailing slash so that joined paths do not double it', () => {
    expect(
      readEnvironment({ ...valid, API_BASE_URL: 'https://api.example.com///' })
        .apiBaseUrl,
    ).toBe('https://api.example.com');
  });

  it('falls back to english when no locale is configured', () => {
    expect(
      readEnvironment({ ...valid, DEFAULT_LOCALE: undefined }).defaultLocale,
    ).toBe('en');
  });
});
