import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React from 'react';

import { HomeScreen } from '../screens/HomeScreen';
import { theme } from '../theme';
import { ROUTES, type RootStackParamList } from './routes';

const Stack = createNativeStackNavigator<RootStackParamList>();

/**
 * The navigation foundation.
 *
 * One screen today. It exists now so that phase 2's screens are added to a structure rather
 * than introducing one, and so the typed route map is in use from the first screen.
 */
export function RootNavigator(): React.JSX.Element {
  return (
    <NavigationContainer
      theme={{
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
          regular: { fontFamily: 'System', fontWeight: '400' },
          medium: { fontFamily: 'System', fontWeight: '500' },
          bold: { fontFamily: 'System', fontWeight: '700' },
          heavy: { fontFamily: 'System', fontWeight: '800' },
        },
      }}
    >
      <Stack.Navigator screenOptions={{ headerShown: false }}>
        <Stack.Screen name={ROUTES.home} component={HomeScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
