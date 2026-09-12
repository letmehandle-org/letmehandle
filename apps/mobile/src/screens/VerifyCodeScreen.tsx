import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { describeFailure } from '../api/messages';
import { useSession } from '../auth/SessionProvider';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { Screen } from '../components/Screen';

interface Props {
  readonly challengeId: string;
  readonly phoneNumber: string;
  readonly onBack: () => void;
}

/** Where the code is entered. Signing in happens here; the navigator follows the session. */
export function VerifyCodeScreen({
  challengeId,
  phoneNumber,
  onBack,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { signIn } = useSession();

  const [code, setCode] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (): void => {
    setProblem(null);
    setBusy(true);

    signIn(challengeId, code.trim())
      .catch((error: unknown) => {
        // Wrong, expired, already used and never existed all arrive the same way,
        // deliberately: saying which would tell somebody guessing how close they are.
        setProblem(describeFailure(error, t, { refused: t('code.invalid') }));
        // Cleared on failure so the next attempt starts from an empty field rather than from
        // a code that has already been refused.
        setCode('');
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <Screen
      title={t('code.title')}
      subtitle={t('code.subtitle', { number: phoneNumber })}
      testID="code-screen"
    >
      <Field
        label={t('code.label')}
        value={code}
        onChangeText={setCode}
        problem={problem}
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete="one-time-code"
        maxLength={6}
        autoFocus
        testID="code-input"
      />
      <Button
        label={t('common.continue')}
        onPress={submit}
        busy={busy}
        disabled={code.trim().length < 4}
        testID="code-continue"
      />
      <Button
        label={t('common.back')}
        variant="quiet"
        onPress={onBack}
        testID="code-back"
      />
    </Screen>
  );
}
