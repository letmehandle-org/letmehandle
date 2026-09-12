/**
 * Jest, with the coverage floor from D-020.
 *
 * The floor applies to logic — hooks, state, services, clients — and not to presentational
 * components or platform glue, where a unit test asserts the mock rather than the behaviour.
 * The exclusions below are that rule made explicit rather than left to judgement.
 */
module.exports = {
  preset: '@react-native/jest-preset',
  // Matchers such as toBeOnTheScreen are built into the testing library from v12.4; a
  // separate extend-expect import was removed upstream.
  setupFiles: ['<rootDir>/jest.setup.js'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json', 'node'],
  transformIgnorePatterns: [
    'node_modules/(?!(?:@react-native|react-native|@react-navigation|react-native-.*)/)',
  ],
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/__tests__/**',
    '!src/theme/**',
    '!src/i18n/locales/**',
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
