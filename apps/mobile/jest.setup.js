// Native modules that have no JavaScript implementation in a test environment.
//
// react-native-config reads values injected by the native build. Under Jest there is no native
// build, so the module returns an empty object and every configuration test would fail for a
// reason that has nothing to do with what it is testing.
jest.mock('react-native-config', () => ({
  __esModule: true,
  default: {
    APP_ENV: 'development',
    API_BASE_URL: 'http://localhost:8000',
    DEFAULT_LOCALE: 'en',
  },
}));

jest.mock('react-native-screens', () => {
  const actual = jest.requireActual('react-native-screens');
  return { ...actual, enableScreens: jest.fn() };
});

// SafeAreaProvider withholds its children until the native side reports the insets, which
// never happens in a test environment — so without this the whole application tree renders as
// an empty provider. The library ships this mock for exactly that reason.
jest.mock(
  'react-native-safe-area-context',
  () => require('react-native-safe-area-context/jest/mock').default,
);
