import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { describeFailure } from '../api/messages';
import { useSession } from '../auth/SessionProvider';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { HeadingBlock } from '../components/HeadingBlock';
import { Screen } from '../components/Screen';

interface Props {
  readonly onCodeSent: (challengeId: string, phoneNumber: string) => void;
  readonly onBack: () => void;
}

/**
 * Where somebody gives the number their assistant will answer for.
 *
 * The number is sent as typed. Normalisation is the backend's, in one place, so that the app
 * and the server cannot disagree about what counts as the same number — which is how one
 * person ends up with two accounts.
 */
export function PhoneNumberScreen({
  onCodeSent,
  onBack,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { requestCode } = useSession();

  const [number, setNumber] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (): void => {
    setProblem(null);
    setBusy(true);

    requestCode(number.trim())
      .then(challengeId => {
        onCodeSent(challengeId, number.trim());
      })
      .catch((error: unknown) => {
        setProblem(
          describeFailure(error, t, {
            refused: t('phone.invalid'),
            rateLimited: t('phone.rateLimited'),
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
          disabled={number.trim().length < 5}
          testID="phone-continue"
        />
      }
    >
      <HeadingBlock title={t('phone.title')} subtitle={t('phone.subtitle')} />
      <Field
        label={t('phone.label')}
        placeholder={t('phone.placeholder')}
        value={number}
        onChangeText={setNumber}
        problem={problem}
        keyboardType="phone-pad"
        textContentType="telephoneNumber"
        autoComplete="tel"
        autoFocus
        testID="phone-input"
      />
    </Screen>
  );
}
