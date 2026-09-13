/** Jest, with the D-020 coverage floor on logic rather than presentation or platform glue. */
module.exports = {
  preset: '@react-native/jest-preset',
  // Matchers such as toBeOnTheScreen come built into the testing library.
  setupFiles: ['<rootDir>/jest.setup.js'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json', 'node'],
  // Shared fixtures live beside the suites without being suites.
  testPathIgnorePatterns: ['/node_modules/', '/__tests__/support/'],
  transformIgnorePatterns: [
    'node_modules/(?!(?:@react-native|react-native|@react-navigation|react-native-.*)/)',
  ],
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/__tests__/**',
    '!src/theme/**',
    '!src/i18n/locales/**',
    // Presentational components are rendered by every screen test and not measured (D-020).
    '!src/components/**',
  ],
  coverageThreshold: {
    global: {
      statements: 90,
      branches: 90,
      functions: 90,
      lines: 90,
    },
  },
};
