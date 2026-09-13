import {
  NavigationContainer,
  type Theme as NavigationTheme,
} from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { useSession } from '../auth/SessionProvider';
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
import { AccountScreen } from '../screens/settings/AccountScreen';
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
};

/**
 * Which application somebody sees.
 *
 * The stacks are mutually exclusive, so a signed-out person has no route to the application, a
 * signed-in one has no route back to the sign-in screens, and somebody who has not finished
 * setting up has no route past it. Signing out does not navigate: the session changes and the
 * tree changes with it, which means there is no state left over from the previous account to
 * leak into the next.
 *
 * Preferences are loaded above the container rather than inside it, because the loading and
 * unavailable states are not navigators and a container whose child is not one has no screen to
 * show.
 */
export function RootNavigator(): React.JSX.Element {
  const { status } = useSession();

  if (status === 'restoring') {
    // Shown rather than flashing the sign-in screen at somebody who is already signed in.
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
      <NavigationContainer theme={navigationTheme}>
        <SignedIn />
      </NavigationContainer>
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
            onCodeSent={(challengeId, phoneNumber) => {
              navigation.navigate(AUTH_ROUTES.verifyCode, {
                challengeId,
                phoneNumber,
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
            onBack={() => {
              navigation.goBack();
            }}
          />
        )}
      </AuthStack.Screen>
    </AuthStack.Navigator>
  );
}

/**
 * Setting up, just finished, or using the thing.
 *
 * The server decides between the first and last, through `next_step`. Holding that on the server
 * is what lets somebody who reinstalls carry on where they were. "Just finished" is this device's
 * alone: it is shown once, when setup ends in front of the user, and never to somebody who opens
 * an account that was set up elsewhere.
 */
function SignedIn(): React.JSX.Element {
  const { onboarding } = usePreferences();
  const step = onboarding.next_step;

  const previous = useRef(step);
  const [justFinished, setJustFinished] = useState(false);

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
          />
        )}
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
