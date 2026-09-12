import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { useSession } from '../auth/SessionProvider';
import { HomeScreen } from '../screens/HomeScreen';
import { PhoneNumberScreen } from '../screens/PhoneNumberScreen';
import { ProfileScreen } from '../screens/ProfileScreen';
import { VerifyCodeScreen } from '../screens/VerifyCodeScreen';
import { WelcomeScreen } from '../screens/WelcomeScreen';
import { theme } from '../theme';
import {
  APP_ROUTES,
  AUTH_ROUTES,
  type AppStackParamList,
  type AuthStackParamList,
} from './routes';

const AuthStack = createNativeStackNavigator<AuthStackParamList>();
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
 * The stacks are mutually exclusive, so a signed-out person has no route to the application
 * and a signed-in one has no route back to the sign-in screens. Signing out does not navigate:
 * the session changes and the tree changes with it, which means there is no state left over
 * from the previous account to leak into the next.
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

  return (
    <NavigationContainer theme={navigationTheme}>
      {status === 'signed-in' ? <SignedIn /> : <SignedOut />}
    </NavigationContainer>
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

function SignedIn(): React.JSX.Element {
  return (
    <AppStack.Navigator screenOptions={{ headerShown: false }}>
      <AppStack.Screen name={APP_ROUTES.home}>
        {({ navigation }) => (
          <HomeScreen
            onOpenProfile={() => {
              navigation.navigate(APP_ROUTES.profile);
            }}
          />
        )}
      </AppStack.Screen>
      <AppStack.Screen name={APP_ROUTES.profile} component={ProfileScreen} />
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
