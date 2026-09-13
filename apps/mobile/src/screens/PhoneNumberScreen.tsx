import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text } from 'react-native';

import { describeFailure } from '../api/messages';
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
  type CountryCode,
  type DeviceHints,
} from '../auth/countries';
import { useSession, type CodeSent } from '../auth/SessionProvider';
import { waitWords } from '../auth/wait';
import { ApiError } from '../api/errors';
import NativeDeviceCountry from '../calls/native/NativeDeviceCountry';
import { Button } from '../components/Button';
import { CountrySheet } from '../components/CountrySheet';
import { Field } from '../components/Field';
import { HeadingBlock } from '../components/HeadingBlock';
import { Icon } from '../components/icon/Icon';
import { Screen } from '../components/Screen';
import { theme } from '../theme';

interface Props {
  readonly onCodeSent: (sent: CodeSent, phoneNumber: string) => void;
  readonly onBack: () => void;
  /** Where the phone is, for choosing the country to start from. Read from the device by default. */
  readonly hints?: DeviceHints;
}

function deviceHints(): DeviceHints {
  return {
    network: NativeDeviceCountry?.networkCountry() ?? null,
    locale: Intl.DateTimeFormat().resolvedOptions().locale,
  };
}

/**
 * Where somebody gives the number their assistant will answer for.
 *
 * The country is chosen for them from where the phone is, shown as its flag and calling code, so
 * all they type is their own number. Continue stays off until the digits are a whole number for
 * that country, which catches a digit too many or too few before a code goes nowhere. The number
 * is sent in E.164; the backend still normalises it, in one place.
 */
export function PhoneNumberScreen({
  onCodeSent,
  onBack,
  hints,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { requestCode } = useSession();

  const detected = useMemo(
    () => detectCountry(hints ?? deviceHints()),
    [hints],
  );
  const [code, setCode] = useState<CountryCode>(detected);
  const [digits, setDigits] = useState('');
  const [picking, setPicking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const country = countryFor(code) as Country;
  const wrong = numberProblem(country, digits);
  const [shortest, longest] = country.digits;
  const length =
    shortest === longest
      ? t('phone.digits', { count: shortest })
      : t('phone.digitsRange', { min: shortest, max: longest });
  const hint =
    wrong === 'long'
      ? t('phone.tooLong', { digits: length })
      : wrong === 'start'
      ? t('phone.wrongStart', { country: t(`phone.countries.${code}`) })
      : null;

  const change = (typed: string): void => {
    setProblem(null);
    // A whole international number pasted or autofilled picks its own country.
    const split = splitInternational(typed, country);
    if (split !== null) {
      setCode(split.country.code);
      setDigits(split.digits.slice(0, split.country.digits[1] + 1));
      return;
    }
    setDigits(nationalDigits(typed).slice(0, longest + 1));
  };

  const submit = (): void => {
    const number = toE164(country, digits);
    setProblem(null);
    setBusy(true);
    requestCode(number)
      .then(sent => {
        onCodeSent(sent, number);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.code === 'unserved_country') {
          setProblem(t('phone.unserved'));
          return;
        }
        setProblem(
          describeFailure(error, t, {
            refused: t('phone.invalid'),
            rateLimited:
              error instanceof ApiError && error.retryAfterSeconds !== undefined
                ? t('phone.rateLimitedFor', {
                    wait: waitWords(error.retryAfterSeconds, t),
                  })
                : t('phone.rateLimited'),
          }),
        );
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <Screen
      onBack={onBack}
      testID="phone-screen"
      footer={
        <Button
          label={t('common.continue')}
          onPress={submit}
          busy={busy}
          disabled={wrong !== null}
          testID="phone-continue"
        />
      }
    >
      <HeadingBlock title={t('phone.title')} subtitle={t('phone.subtitle')} />
      <Field
        label={t('phone.label')}
        placeholder={grouped(country, '0'.repeat(longest))}
        value={grouped(country, digits)}
        onChangeText={change}
        problem={problem ?? hint}
        keyboardType="phone-pad"
        textContentType="telephoneNumber"
        autoComplete="tel"
        autoFocus
        testID="phone-input"
        leading={
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`${t('phone.country')}: ${t(
              'phone.countryValue',
              {
                country: t(`phone.countries.${code}`),
                dial: country.dial,
              },
            )}`}
            onPress={() => {
              setPicking(true);
            }}
            hitSlop={8}
            style={styles.country}
            testID="phone-country"
          >
            <Text style={styles.flag}>{flagOf(code)}</Text>
            <Text style={styles.dial}>+{country.dial}</Text>
            <Icon name="chev-d" size={16} colour={theme.colour.textMuted} />
          </Pressable>
        }
      />
      <CountrySheet
        visible={picking}
        selected={code}
        onPick={picked => {
          setCode(picked);
          setPicking(false);
          setProblem(null);
          setDigits(current =>
            current.slice(
              0,
              (countryFor(picked) ?? COUNTRIES[0]).digits[1] + 1,
            ),
          );
        }}
        onClose={() => {
          setPicking(false);
        }}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  country: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.xs,
    paddingRight: theme.space.row,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: theme.colour.border,
  },
  flag: { fontSize: 22 },
  dial: {
    ...theme.type.body,
    color: theme.colour.text,
    fontVariant: ['tabular-nums'],
  },
});
