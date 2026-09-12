module.exports = {
  root: true,
  extends: '@react-native',
  ignorePatterns: ['coverage/', 'android/', 'ios/', 'node_modules/'],
  overrides: [
    {
      // Jest's globals exist in the test environment and in the files that configure it, and
      // nowhere else. Declaring it per file rather than globally keeps `jest` from being
      // silently available in application code.
      files: [
        'jest.setup.js',
        'jest.config.js',
        'src/**/__tests__/**/*.{ts,tsx}',
      ],
      env: { jest: true },
    },
  ],
};
