// Native modules with no JavaScript implementation under Jest; react-native-config gets fixed values.
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

// SafeAreaProvider renders its children only once insets arrive, so its shipped mock supplies them.
jest.mock(
  'react-native-safe-area-context',
  () => require('react-native-safe-area-context/jest/mock').default,
);

// The keychain is native, so its functions are mocked for every suite.
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
