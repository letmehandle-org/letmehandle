import {
  NavigationContainer,
  type Theme as NavigationTheme,
} from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { useSession } from '../auth/SessionProvider';
import { CallScreeningProvider } from '../calls/CallScreeningProvider';
import { CallScreeningScreen } from '../calls/CallScreeningScreen';
import { callScreening, type CallScreening } from '../calls/callScreening';
import {
  PreferencesProvider,
  usePreferences,
} from '../preferences/PreferencesProvider';
import { MainTabs } from '../screens/MainTabs';
import { PhoneNumberScreen } from '../screens/PhoneNumberScreen';
import { SetupDoneScreen } from '../screens/SetupDoneScreen';
import { SetupScreen } from '../screens/SetupScreen';
import type { SettingsPage } from '../screens/SettingsScreen';
import { VerifyCodeScreen } from '../screens/VerifyCodeScreen';
import { WelcomeScreen } from '../screens/WelcomeScreen';
import { CallDetailScreen } from '../screens/calls/CallDetailScreen';
import { EscalationScreen } from '../screens/calls/EscalationScreen';
import { TranscriptScreen } from '../screens/calls/TranscriptScreen';
import { AccountScreen } from '../screens/settings/AccountScreen';
import { PrivacyScreen } from '../screens/settings/PrivacyScreen';
import { AuthorityScreen } from '../screens/settings/AuthorityScreen';
import { HoursScreen } from '../screens/settings/HoursScreen';
import { PersonaliseScreen } from '../screens/settings/PersonaliseScreen';
import { SayScreen } from '../screens/settings/SayScreen';
import { TopicsScreen } from '../screens/settings/TopicsScreen';
import { WhenCalledScreen } from '../screens/settings/WhenCalledScreen';
import { WhoGetsThroughScreen } from '../screens/settings/WhoGetsThroughScreen';
import { theme } from '../theme';
import {
  APP_ROUTES,
  AUTH_ROUTES,
  ONBOARDING_ROUTES,
  type AppStackParamList,
  type AuthStackParamList,
  type OnboardingStackParamList,
} from './routes';

const AuthStack = createNativeStackNavigator<AuthStackParamList>();
const OnboardingStack = createNativeStackNavigator<OnboardingStackParamList>();
const AppStack = createNativeStackNavigator<AppStackParamList>();

const navigationTheme: NavigationTheme = {
  dark: false,
  colors: {
    primary: theme.colour.accent,
    background: theme.colour.background,
    card: theme.colour.surface,
    text: theme.colour.text,
    border: theme.colour.border,
    notification: theme.colour.needsYouDeep,
  },
  fonts: {
    regular: { fontFamily: theme.font.regular, fontWeight: '400' },
    medium: { fontFamily: theme.font.regular, fontWeight: '400' },
    bold: { fontFamily: theme.font.strong, fontWeight: '600' },
    heavy: { fontFamily: theme.font.strong, fontWeight: '600' },
  },
};

const SETTINGS_ROUTES: Record<SettingsPage, keyof AppStackParamList> = {
  who: APP_ROUTES.who,
  when: APP_ROUTES.when,
  hours: APP_ROUTES.hours,
  authority: APP_ROUTES.authority,
  say: APP_ROUTES.say,
  personalise: APP_ROUTES.personalise,
  account: APP_ROUTES.account,
  privacy: APP_ROUTES.privacy,
  callScreening: APP_ROUTES.callScreening,
};

/** The signed-out, setting-up or signed-in stacks, chosen by the session and never navigated between. */
export function RootNavigator({
  screening = callScreening,
}: {
  /** This handset's call screening, or null where it has none. */
  readonly screening?: CallScreening | null;
}): React.JSX.Element {
  const { status } = useSession();

  useEffect(() => {
    // Forgets the previous account's rules and calls on the handset.
    if (status === 'signed-out' && screening !== null) {
      screening.forgetAccount().catch(() => undefined);
    }
  }, [status, screening]);

  if (status === 'restoring') {
    return (
      <View style={styles.restoring} testID="restoring">
        <ActivityIndicator color={theme.colour.accent} />
      </View>
    );
  }

  if (status === 'signed-out') {
    return (
      <NavigationContainer theme={navigationTheme}>
        <SignedOut />
      </NavigationContainer>
    );
  }

  return (
    <PreferencesProvider>
      <CallScreeningProvider screening={screening}>
        <NavigationContainer theme={navigationTheme}>
          <SignedIn screening={screening} />
        </NavigationContainer>
      </CallScreeningProvider>
    </PreferencesProvider>
  );
}

function SignedOut(): React.JSX.Element {
  return (
    <AuthStack.Navigator screenOptions={{ headerShown: false }}>
      <AuthStack.Screen name={AUTH_ROUTES.welcome}>
        {({ navigation }) => (
          <WelcomeScreen
            onStart={() => {
              navigation.navigate(AUTH_ROUTES.phoneNumber);
            }}
          />
        )}
      </AuthStack.Screen>

      <AuthStack.Screen name={AUTH_ROUTES.phoneNumber}>
        {({ navigation }) => (
          <PhoneNumberScreen
            onBack={() => {
              navigation.goBack();
            }}
            onCodeSent={(sent, phoneNumber) => {
              navigation.navigate(AUTH_ROUTES.verifyCode, {
                challengeId: sent.challengeId,
                phoneNumber,
                resendAfterSeconds: sent.resendAfterSeconds,
              });
            }}
          />
        )}
      </AuthStack.Screen>

      <AuthStack.Screen name={AUTH_ROUTES.verifyCode}>
        {({ navigation, route }) => (
          <VerifyCodeScreen
            challengeId={route.params.challengeId}
            phoneNumber={route.params.phoneNumber}
            resendAfterSeconds={route.params.resendAfterSeconds}
            onBack={() => {
              navigation.goBack();
            }}
          />
        )}
      </AuthStack.Screen>
    </AuthStack.Navigator>
  );
}

/** Setup while the server has a next step, then the done screen once, then the app. */
function SignedIn({
  screening,
}: {
  readonly screening: CallScreening | null;
}): React.JSX.Element {
  const { onboarding } = usePreferences();
  const step = onboarding.next_step;

  const previous = useRef(step);
  const [justFinished, setJustFinished] = useState(false);
  // Bumped when a call is deleted, so the list reloads without it.
  const [historyVersion, setHistoryVersion] = useState(0);

  useEffect(() => {
    if (previous.current !== null && step === null) {
      setJustFinished(true);
    }
    previous.current = step;
  }, [step]);

  if (step !== null) {
    return (
      <OnboardingStack.Navigator screenOptions={{ headerShown: false }}>
        <OnboardingStack.Screen name={ONBOARDING_ROUTES.step}>
          {() => <SetupScreen step={step} />}
        </OnboardingStack.Screen>
      </OnboardingStack.Navigator>
    );
  }

  if (justFinished) {
    return (
      <SetupDoneScreen
        onDone={() => {
          setJustFinished(false);
        }}
      />
    );
  }

  return (
    <AppStack.Navigator screenOptions={{ headerShown: false }}>
      <AppStack.Screen name={APP_ROUTES.tabs}>
        {({ navigation }) => (
          <MainTabs
            onOpenSetting={page => {
              navigation.navigate(SETTINGS_ROUTES[page]);
            }}
            onOpenCall={callId => {
              navigation.navigate(APP_ROUTES.call, { callId });
            }}
            onOpenEscalation={callId => {
              navigation.navigate(APP_ROUTES.escalation, { callId });
            }}
            historyVersion={historyVersion}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.call}>
        {({ navigation, route }) => (
          <CallDetailScreen
            callId={route.params.callId}
            onBack={navigation.goBack}
            onOpenTranscript={callId => {
              navigation.navigate(APP_ROUTES.transcript, { callId });
            }}
            onOpenEscalation={callId => {
              navigation.navigate(APP_ROUTES.escalation, { callId });
            }}
            onDeleted={() => {
              setHistoryVersion(version => version + 1);
              navigation.goBack();
            }}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.transcript}>
        {({ navigation, route }) => (
          <TranscriptScreen
            callId={route.params.callId}
            onBack={navigation.goBack}
            onOpenPrivacy={() => {
              navigation.navigate(APP_ROUTES.privacy);
            }}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.escalation}>
        {({ navigation, route }) => (
          <EscalationScreen
            callId={route.params.callId}
            onBack={navigation.goBack}
            onOpenSummary={callId => {
              navigation.navigate(APP_ROUTES.call, { callId });
            }}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.privacy}>
        {({ navigation }) => <PrivacyScreen onBack={navigation.goBack} />}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.who}>
        {({ navigation }) => (
          <WhoGetsThroughScreen onBack={navigation.goBack} />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.when}>
        {({ navigation }) => <WhenCalledScreen onBack={navigation.goBack} />}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.hours}>
        {({ navigation }) => <HoursScreen onBack={navigation.goBack} />}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.authority}>
        {({ navigation }) => <AuthorityScreen onBack={navigation.goBack} />}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.say}>
        {({ navigation }) => <SayScreen onBack={navigation.goBack} />}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.personalise}>
        {({ navigation }) => (
          <PersonaliseScreen
            onBack={navigation.goBack}
            onOpenTopics={() => {
              navigation.navigate(APP_ROUTES.topics);
            }}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.topics}>
        {({ navigation }) => <TopicsScreen onBack={navigation.goBack} />}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.account}>
        {({ navigation }) => <AccountScreen onBack={navigation.goBack} />}
      </AppStack.Screen>
      {screening !== null && (
        <AppStack.Screen name={APP_ROUTES.callScreening}>
          {({ navigation }) => (
            <CallScreeningScreen
              screening={screening}
              onBack={navigation.goBack}
            />
          )}
        </AppStack.Screen>
      )}
    </AppStack.Navigator>
  );
}

const styles = StyleSheet.create({
  restoring: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colour.background,
  },
});
