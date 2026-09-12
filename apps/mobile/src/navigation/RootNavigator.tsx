import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { useSession } from '../auth/SessionProvider';
import { CallScreeningProvider } from '../calls/CallScreeningProvider';
import { CallScreeningScreen } from '../calls/CallScreeningScreen';
import { callScreening, type CallScreening } from '../calls/callScreening';
import {
  PreferencesProvider,
  usePreferences,
} from '../preferences/PreferencesProvider';
import { HomeScreen } from '../screens/HomeScreen';
import { OnboardingScreen } from '../screens/OnboardingScreen';
import { PhoneNumberScreen } from '../screens/PhoneNumberScreen';
import { ProfileScreen } from '../screens/ProfileScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { SettingsSectionScreen } from '../screens/SettingsSectionScreen';
import { VerifyCodeScreen } from '../screens/VerifyCodeScreen';
import { WelcomeScreen } from '../screens/WelcomeScreen';
import { theme } from '../theme';
import { VoiceScreen } from '../voice/VoiceScreen';
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

const navigationTheme = {
  dark: true,
  colors: {
    primary: theme.colour.accent,
    background: theme.colour.background,
    card: theme.colour.surface,
    text: theme.colour.text,
    border: theme.colour.border,
    notification: theme.colour.accent,
  },
  fonts: {
    regular: { fontFamily: 'System', fontWeight: '400' as const },
    medium: { fontFamily: 'System', fontWeight: '500' as const },
    bold: { fontFamily: 'System', fontWeight: '700' as const },
    heavy: { fontFamily: 'System', fontWeight: '800' as const },
  },
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
export function RootNavigator({
  screening = callScreening,
}: {
  /** This handset's call screening, or null where it has none. A parameter so tests can vary it. */
  readonly screening?: CallScreening | null;
}): React.JSX.Element {
  const { status } = useSession();

  useEffect(() => {
    // Whoever signs in next must not be screened by the rules of whoever left, nor have the
    // previous account's calls reported as theirs.
    if (status === 'signed-out' && screening !== null) {
      screening.forgetAccount().catch(() => undefined);
    }
  }, [status, screening]);

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
 * Setting up, or using the thing.
 *
 * The server decides which, through `next_step`. Holding that here rather than on the device is
 * what lets somebody who reinstalls carry on where they were instead of answering everything a
 * second time.
 */
function SignedIn({
  screening,
}: {
  readonly screening: CallScreening | null;
}): React.JSX.Element {
  const { onboarding } = usePreferences();
  const step = onboarding.next_step;

  if (step !== null) {
    return (
      <OnboardingStack.Navigator screenOptions={{ headerShown: false }}>
        <OnboardingStack.Screen name={ONBOARDING_ROUTES.step}>
          {() => <OnboardingScreen step={step} />}
        </OnboardingStack.Screen>
      </OnboardingStack.Navigator>
    );
  }

  return (
    <AppStack.Navigator screenOptions={{ headerShown: false }}>
      <AppStack.Screen name={APP_ROUTES.home}>
        {({ navigation }) => (
          <HomeScreen
            onOpenProfile={() => {
              navigation.navigate(APP_ROUTES.profile);
            }}
            onOpenSettings={() => {
              navigation.navigate(APP_ROUTES.settings);
            }}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.profile} component={ProfileScreen} />
      <AppStack.Screen name={APP_ROUTES.settings}>
        {({ navigation }) => (
          <SettingsScreen
            onOpenSection={section => {
              navigation.navigate(APP_ROUTES.settingsSection, { section });
            }}
            onOpenVoice={() => {
              navigation.navigate(APP_ROUTES.voice);
            }}
            onOpenCallScreening={
              screening === null
                ? null
                : () => {
                    navigation.navigate(APP_ROUTES.callScreening);
                  }
            }
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.settingsSection}>
        {({ route }) => (
          <SettingsSectionScreen section={route.params.section} />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.voice} component={VoiceScreen} />
      {screening !== null && (
        <AppStack.Screen name={APP_ROUTES.callScreening}>
          {() => <CallScreeningScreen screening={screening} />}
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
