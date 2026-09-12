/**
 * The only place this application reads its environment.
 *
 * Everything else imports `environment`. Scattering `Config.SOMETHING` through the codebase
 * makes it impossible to answer what the app is configured by, and means a missing value is
 * discovered by a blank screen rather than at startup.
 */
import Config from 'react-native-config';

export type AppEnvironment = 'development' | 'staging' | 'production';

export interface Environment {
  readonly name: AppEnvironment;
  /** Base URL of the backend, without a trailing slash. */
  readonly apiBaseUrl: string;
  /** BCP 47 tag used for the interface and passed to the agent. */
  readonly defaultLocale: string;
}

export class ConfigurationError extends Error {
  constructor(variable: string, reason: string) {
    super(`${variable} ${reason}. See .env.example.`);
    this.name = 'ConfigurationError';
  }
}

const ENVIRONMENTS: readonly AppEnvironment[] = [
  'development',
  'staging',
  'production',
];

function requireString(variable: string, value: string | undefined): string {
  const trimmed = value?.trim();
  if (trimmed === undefined || trimmed.length === 0) {
    throw new ConfigurationError(variable, 'is required but was not set');
  }
  return trimmed;
}

function parseEnvironmentName(value: string | undefined): AppEnvironment {
  const name = (value?.trim() ?? 'development') as AppEnvironment;
  if (!ENVIRONMENTS.includes(name)) {
    throw new ConfigurationError(
      'APP_ENV',
      `must be one of ${ENVIRONMENTS.join(', ')}`,
    );
  }
  return name;
}

function parseUrl(variable: string, value: string | undefined): string {
  const url = requireString(variable, value);
  if (!/^https?:\/\//.test(url)) {
    throw new ConfigurationError(variable, 'must be an http or https URL');
  }
  return url.replace(/\/+$/, '');
}

/**
 * Build the environment from raw values.
 *
 * Exported separately from the singleton so a test can supply values without touching the
 * native module, and so the validation itself is testable rather than only observable through
 * a failing app.
 */
export function readEnvironment(
  raw: Record<string, string | undefined>,
): Environment {
  return {
    name: parseEnvironmentName(raw.APP_ENV),
    apiBaseUrl: parseUrl('API_BASE_URL', raw.API_BASE_URL),
    defaultLocale: requireString('DEFAULT_LOCALE', raw.DEFAULT_LOCALE ?? 'en'),
  };
}

export const environment: Environment = readEnvironment(
  Config as Record<string, string | undefined>,
);
