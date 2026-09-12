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

// The keychain is native. Under Jest there is nothing behind it, so the token store would
// throw on every read — which its own tests cover deliberately, but which would make every
// other test fail for a reason unrelated to what it is testing.
jest.mock('react-native-keychain', () => ({
  __esModule: true,
  STORAGE_TYPE: { AES_GCM: 'KeystoreAESGCM' },
  ACCESSIBLE: {
    AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: 'AfterFirstUnlockThisDeviceOnly',
  },
  setGenericPassword: jest.fn(async () => true),
  getGenericPassword: jest.fn(async () => false),
  resetGenericPassword: jest.fn(async () => true),
}));
