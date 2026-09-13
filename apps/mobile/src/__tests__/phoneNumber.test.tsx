/**
 * Entering a number: the country chosen from where the phone is, and only whole numbers sent.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import {
  COUNTRIES,
  countryFor,
  detectCountry,
  flagOf,
  grouped,
  nationalDigits,
  numberProblem,
  splitInternational,
  toE164,
  type Country,
} from '../auth/countries';
import { en } from '../i18n/locales/en';
import { jsonResponse } from './support/http';

const mockNetwork: { country: string | null } = { country: 'in' };
jest.mock('../calls/native/NativeDeviceCountry', () => ({
  __esModule: true,
  get default() {
    return { networkCountry: () => mockNetwork.country };
  },
}));

const INDIA = countryFor('IN') as Country;
const US = countryFor('US') as Country;
// Ten digits a mobile number in India could have. Written without its country code, and joined to
// one only at run time, so no whole international number sits in the source.
const INDIAN_DIGITS = ['98765', '00000'].join('');

describe('the rules for each country', () => {
  it('covers a dozen countries, each with a flag and a calling code', () => {
    expect(COUNTRIES.length).toBeGreaterThanOrEqual(12);
    expect(new Set(COUNTRIES.map(country => country.code)).size).toBe(
      COUNTRIES.length,
    );
    expect(flagOf('IN')).toBe('🇮🇳');
    for (const country of COUNTRIES) {
      expect(en.phone.countries[country.code]).toEqual(expect.any(String));
    }
  });

  it('takes India as exactly ten digits starting 6 to 9', () => {
    expect(numberProblem(INDIA, INDIAN_DIGITS)).toBeNull();
    expect(numberProblem(INDIA, INDIAN_DIGITS.slice(0, 9))).toBe('short');
    expect(numberProblem(INDIA, `${INDIAN_DIGITS}1`)).toBe('long');
    expect(numberProblem(INDIA, '1234567890')).toBe('start');
    // An unfinished number is not called wrong before its fourth digit.
    expect(numberProblem(INDIA, '123')).toBe('short');
  });

  it.each([
    ['US', '2025550143', null],
    ['US', '1025550143', 'start'],
    ['GB', '7700900123', null],
    ['AU', '412345678', null],
    ['AE', '501234567', null],
    ['SG', '81234567', null],
    ['DE', '15123456789', null],
    ['DE', '1512345678', null],
    ['FR', '612345678', null],
    ['JP', '9012345678', null],
    ['BR', '11912345678', null],
    ['ZA', '821234567', null],
    ['SA', '512345678', null],
    ['CA', '4165550199', null],
  ] as const)('%s %s is %s', (code, digits, problem) => {
    expect(numberProblem(countryFor(code) as Country, digits)).toBe(problem);
  });

  it('drops decoration and a trunk zero, and groups digits the usual way', () => {
    expect(nationalDigits(' 0 98765-00000 ')).toBe(INDIAN_DIGITS);
    expect(grouped(INDIA, INDIAN_DIGITS)).toBe('98765 00000');
    expect(grouped(US, '20255')).toBe('202 55');
    expect(grouped(countryFor('SG') as Country, '812345678')).toBe(
      '8123 4567 8',
    );
    expect(toE164(US, '2025550143')).toBe('+12025550143');
  });

  it('reads a pasted international number into its country, keeping the chosen one of a shared code', () => {
    expect(splitInternational('+1 202 555 0143', INDIA)).toEqual({
      country: US,
      digits: '2025550143',
    });
    const canada = countryFor('CA') as Country;
    expect(splitInternational('+1 202 555 0143', canada)?.country).toBe(canada);
    expect(splitInternational(`00971${'501234567'}`, US)?.country.code).toBe(
      'AE',
    );
    expect(splitInternational('98765', US)).toBeNull();
    expect(splitInternational('+999', US)).toBeNull();
  });

  it('starts from the network, then the region setting, then a default', () => {
    expect(detectCountry({ network: 'in', locale: 'en-US' })).toBe('IN');
    expect(detectCountry({ network: 'xx', locale: 'en-GB' })).toBe('GB');
    expect(detectCountry({ network: null, locale: 'de_DE' })).toBe('DE');
    expect(detectCountry({ network: null, locale: 'en' })).toBe('US');
    expect(detectCountry({})).toBe('US');
  });
});

async function phoneScreen() {
  const view = await render(<App />);
  await fireEvent.press(await view.findByTestId('start-button'));
  await waitFor(() => {
    expect(view.getByTestId('phone-screen')).toBeOnTheScreen();
  });
  return view;
}

describe('the number screen', () => {
  let sent: string[];

  beforeEach(() => {
    mockNetwork.country = 'in';
    sent = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      if (url.endsWith('/v1/auth/challenge')) {
        sent.push(
          (JSON.parse(init?.body as string) as { phone_number: string })
            .phone_number,
        );
        return jsonResponse(202, {
          challenge_id: 'c',
          expires_in_seconds: 300,
          resend_after_seconds: 30,
        });
      }
      return jsonResponse(200, {});
    }) as unknown as typeof fetch;
  });

  it('fills in the country from the network, so only the number is typed', async () => {
    const view = await phoneScreen();

    expect(view.getByTestId('phone-country')).toHaveAccessibleName(
      'Country: India, +91',
    );
    expect(view.getByTestId('phone-continue')).toBeDisabled();

    await fireEvent.changeText(view.getByTestId('phone-input'), INDIAN_DIGITS);
    expect(view.getByTestId('phone-input').props.value).toBe('98765 00000');
    await fireEvent.press(view.getByTestId('phone-continue'));

    await waitFor(() => {
      expect(sent).toEqual([toE164(INDIA, INDIAN_DIGITS)]);
    });
    expect(await view.findByTestId('code-screen')).toBeOnTheScreen();
  });

  it('keeps Continue off and says why for a number that cannot be whole', async () => {
    const view = await phoneScreen();

    await fireEvent.changeText(view.getByTestId('phone-input'), '1234');
    expect(
      view.getByText("Numbers in India don't start like that."),
    ).toBeOnTheScreen();
    expect(view.getByTestId('phone-continue')).toBeDisabled();

    await fireEvent.changeText(
      view.getByTestId('phone-input'),
      `${INDIAN_DIGITS}12`,
    );
    expect(view.getByText('That is more than 10 digits.')).toBeOnTheScreen();
    expect(view.getByTestId('phone-continue')).toBeDisabled();
    expect(sent).toEqual([]);
  });

  it('lets somebody choose another country from the sheet', async () => {
    const view = await phoneScreen();

    await fireEvent.press(view.getByTestId('phone-country'));
    expect(view.getByTestId('country-IN')).toBeChecked();
    await fireEvent.press(view.getByTestId('country-DE'));

    expect(view.getByTestId('phone-country')).toHaveAccessibleName(
      'Country: Germany, +49',
    );
    await fireEvent.changeText(view.getByTestId('phone-input'), '15123456789');
    await fireEvent.press(view.getByTestId('phone-continue'));
    await waitFor(() => {
      expect(sent).toEqual([
        toE164(countryFor('DE') as Country, '15123456789'),
      ]);
    });
  });

  it('closes the sheet without changing anything', async () => {
    const view = await phoneScreen();
    await fireEvent.press(view.getByTestId('phone-country'));
    await fireEvent.press(view.getByTestId('country-sheet-close'));
    expect(view.getByTestId('phone-country')).toHaveAccessibleName(
      'Country: India, +91',
    );
  });

  it('switches country when a whole international number is pasted', async () => {
    const view = await phoneScreen();
    await fireEvent.changeText(
      view.getByTestId('phone-input'),
      '+1 202 555 0143',
    );

    expect(view.getByTestId('phone-country')).toHaveAccessibleName(
      'Country: United States, +1',
    );
    expect(view.getByTestId('phone-input').props.value).toBe('202 555 0143');
    expect(view.getByTestId('phone-continue')).toBeEnabled();
  });

  it('uses the region setting where there is no network to ask', async () => {
    mockNetwork.country = null;
    const view = await phoneScreen();
    expect(view.getByTestId('phone-country')).toHaveAccessibleName(/Country: /);
  });
});
