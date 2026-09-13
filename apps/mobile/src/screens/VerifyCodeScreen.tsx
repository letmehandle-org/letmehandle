import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '../api/errors';
import { describeFailure } from '../api/messages';
import { useSession } from '../auth/SessionProvider';
import { Button } from '../components/Button';
import { CodeInput } from '../components/CodeInput';
import { HeadingBlock } from '../components/HeadingBlock';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { clockWords, rateLimitedWords, waitWords } from '../auth/wait';
import { environment, type AppEnvironment } from '../config/environment';

interface Props {
  readonly challengeId: string;
  readonly phoneNumber: string;
  /** Seconds until another code may be asked for, from when this one was sent. */
  readonly resendAfterSeconds: number;
  readonly onBack: () => void;
}

/**
 * Whether to tell somebody the fixed testing code.
 *
 * FOR TESTING ONLY — remove before launch, together with the backend's fixed code. A
 * development build talks to a backend running the mock code provider, which accepts 123456 for
 * every challenge; no other build is told anything about codes.
 */
export function showsTestingCode(name: AppEnvironment): boolean {
  return name === 'development';
}

/**
 * Where the code is entered. Signing in happens here; the navigator follows the session.
 *
 * Another code can be asked for once the server says so, counted down on the button rather than
 * offered and then refused. Only the newest code works, so the screen says that when it sends one.
 */
export function VerifyCodeScreen({
  challengeId: firstChallenge,
  phoneNumber,
  resendAfterSeconds,
  onBack,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { signIn, requestCode } = useSession();

  const [challengeId, setChallengeId] = useState(firstChallenge);
  const [code, setCode] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [resendAt, setResendAt] = useState(
    () => Date.now() + resendAfterSeconds * 1000,
  );
  const [now, setNow] = useState(() => Date.now());

  const waitLeft = Math.max(0, (resendAt - now) / 1000);
  useEffect(() => {
    if (waitLeft <= 0) {
      return undefined;
    }
    const tick = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(tick);
    };
  }, [waitLeft]);

  const resend = (): void => {
    setProblem(null);
    setResent(false);
    setSending(true);
    requestCode(phoneNumber)
      .then(sent => {
        setChallengeId(sent.challengeId);
        setCode('');
        setResent(true);
        setResendAt(Date.now() + sent.resendAfterSeconds * 1000);
        setNow(Date.now());
      })
      .catch((error: unknown) => {
        if (
          error instanceof ApiError &&
          error.retryAfterSeconds !== undefined
        ) {
          setResendAt(Date.now() + error.retryAfterSeconds * 1000);
          setNow(Date.now());
        }
        setProblem(
          describeFailure(error, t, {
            refused: t('code.invalid'),
            rateLimited: rateLimitedWords(error, t),
          }),
        );
      })
      .finally(() => {
        setSending(false);
      });
  };

  const submit = (): void => {
    setProblem(null);
    setResent(false);
    setBusy(true);

    signIn(challengeId, code.trim())
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.isRateLimited) {
          // The number is locked after too many wrong codes, or this phone has guessed at too
          // many. Either way the wait is the server's to say.
          setProblem(
            t('code.locked', {
              wait: waitWords(error.retryAfterSeconds ?? 3600, t),
            }),
          );
          setCode('');
          return;
        }
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
      onBack={onBack}
      testID="code-screen"
      footer={
        <>
          <Button
            label={t('common.continue')}
            onPress={submit}
            busy={busy}
            disabled={code.trim().length < 4}
            testID="code-continue"
          />
          <Button
            label={
              waitLeft > 0
                ? t('code.resendIn', { clock: clockWords(waitLeft) })
                : t('code.resend')
            }
            variant="quiet"
            onPress={resend}
            busy={sending}
            disabled={waitLeft > 0}
            testID="code-resend"
          />
        </>
      }
    >
      <HeadingBlock
        title={t('code.title')}
        subtitle={t('code.subtitle', { number: phoneNumber })}
      />
      <CodeInput
        label={t('code.label')}
        value={code}
        onChange={value => {
          setCode(value);
          setProblem(null);
        }}
        problem={problem !== null}
        testID="code-input"
      />
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="code-problem" />
      )}
      {resent && problem === null && (
        <Notice tone="done" message={t('code.resent')} testID="code-resent" />
      )}
      {showsTestingCode(environment.name) && (
        <Notice message={t('code.testingHint')} testID="code-testing-hint" />
      )}
    </Screen>
  );
}
