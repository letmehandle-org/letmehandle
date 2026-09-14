/** The countries a number can be entered for and the national number rules each one checks. */

export type CountryCode =
  | 'IN'
  | 'US'
  | 'CA'
  | 'GB'
  | 'AU'
  | 'AE'
  | 'SA'
  | 'SG'
  | 'DE'
  | 'FR'
  | 'JP'
  | 'BR'
  | 'ZA';

export interface Country {
  readonly code: CountryCode;
  /** The calling code, without the plus. */
  readonly dial: string;
  /** How many digits a whole national number has: one length, or the shortest and longest. */
  readonly digits: readonly [number, number];
  /** What a whole national number starts with. */
  readonly starts: RegExp;
  /** How the digits are grouped as they are typed, for reading them back. */
  readonly groups: readonly number[];
}

export const COUNTRIES: readonly Country[] = [
  // Mobile numbers: ten digits starting 6 to 9.
  {
    code: 'IN',
    dial: '91',
    digits: [10, 10],
    starts: /^[6-9]/,
    groups: [5, 5],
  },
  // An area code and an exchange that each cannot start with 0 or 1.
  {
    code: 'US',
    dial: '1',
    digits: [10, 10],
    starts: /^[2-9]\d{2}[2-9]/,
    groups: [3, 3, 4],
  },
  {
    code: 'CA',
    dial: '1',
    digits: [10, 10],
    starts: /^[2-9]\d{2}[2-9]/,
    groups: [3, 3, 4],
  },
  {
    code: 'GB',
    dial: '44',
    digits: [10, 10],
    starts: /^[1237]/,
    groups: [4, 6],
  },
  {
    code: 'AU',
    dial: '61',
    digits: [9, 9],
    starts: /^[23478]/,
    groups: [3, 3, 3],
  },
  { code: 'AE', dial: '971', digits: [9, 9], starts: /^5/, groups: [2, 3, 4] },
  { code: 'SA', dial: '966', digits: [9, 9], starts: /^5/, groups: [2, 3, 4] },
  { code: 'SG', dial: '65', digits: [8, 8], starts: /^[689]/, groups: [4, 4] },
  {
    code: 'DE',
    dial: '49',
    digits: [10, 11],
    starts: /^1[5-7]/,
    groups: [3, 4, 4],
  },
  {
    code: 'FR',
    dial: '33',
    digits: [9, 9],
    starts: /^[1-9]/,
    groups: [1, 2, 2, 2, 2],
  },
  {
    code: 'JP',
    dial: '81',
    digits: [9, 10],
    starts: /^[1-9]/,
    groups: [2, 4, 4],
  },
  {
    code: 'BR',
    dial: '55',
    digits: [10, 11],
    starts: /^[1-9]{2}/,
    groups: [2, 5, 4],
  },
  {
    code: 'ZA',
    dial: '27',
    digits: [9, 9],
    starts: /^[1-8]/,
    groups: [2, 3, 4],
  },
];

export const DEFAULT_COUNTRY: CountryCode = 'US';

export function countryFor(
  code: string | null | undefined,
): Country | undefined {
  const wanted = code?.toUpperCase();
  return COUNTRIES.find(country => country.code === wanted);
}

/** The flag, from the two regional indicator letters every platform draws as one. */
export function flagOf(code: CountryCode): string {
  return String.fromCodePoint(
    ...[...code].map(letter => 0x1f1e6 + letter.charCodeAt(0) - 65),
  );
}

export interface DeviceHints {
  /** The network's country, or the SIM's, as the platform reports it. */
  readonly network?: string | null;
  /** A BCP 47 locale such as `en-IN`. */
  readonly locale?: string | null;
}

/** The country to start from: the network's, else the locale's region, else the default. */
export function detectCountry(hints: DeviceHints): CountryCode {
  const fromNetwork = countryFor(hints.network);
  if (fromNetwork !== undefined) {
    return fromNetwork.code;
  }
  const region = hints.locale
    ?.split(/[-_]/)
    .find(part => /^[A-Za-z]{2}$/.test(part) && part === part.toUpperCase());
  return countryFor(region)?.code ?? DEFAULT_COUNTRY;
}

/** The digits of a national number, without decoration or a trunk `0`. */
export function nationalDigits(typed: string): string {
  return typed.replace(/\D/g, '').replace(/^0+/, '');
}

export type NumberProblem = 'short' | 'long' | 'start' | null;

/** What is wrong with these digits, or null; the start is judged from the fourth digit on. */
export function numberProblem(country: Country, digits: string): NumberProblem {
  const [shortest, longest] = country.digits;
  if (digits.length > longest) {
    return 'long';
  }
  if (digits.length >= 4 && !country.starts.test(digits)) {
    return 'start';
  }
  return digits.length < shortest ? 'short' : null;
}

export function toE164(country: Country, digits: string): string {
  return `+${country.dial}${digits}`;
}

/** The digits in the country's usual groups: "98765 43210". */
export function grouped(country: Country, digits: string): string {
  const parts: string[] = [];
  let at = 0;
  for (const size of country.groups) {
    if (at >= digits.length) {
      break;
    }
    parts.push(digits.slice(at, at + size));
    at += size;
  }
  if (at < digits.length) {
    parts.push(digits.slice(at));
  }
  return parts.join(' ');
}

/** A pasted international number split into country and digits, longest calling code first. */
export function splitInternational(
  typed: string,
  current: Country,
): { country: Country; digits: string } | null {
  const trimmed = typed.trim();
  if (!trimmed.startsWith('+') && !trimmed.startsWith('00')) {
    return null;
  }
  const all = trimmed.replace(/\D/g, '').replace(/^00/, '');
  const matches = COUNTRIES.filter(country =>
    all.startsWith(country.dial),
  ).sort((a, b) => b.dial.length - a.dial.length);
  const first = matches[0];
  if (first === undefined) {
    return null;
  }
  const country =
    matches.find(
      match => match.code === current.code && match.dial === first.dial,
    ) ?? first;
  return { country, digits: nationalDigits(all.slice(country.dial.length)) };
}
