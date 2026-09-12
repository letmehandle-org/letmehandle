/**
 * The two pieces of native setup the app cannot start without.
 *
 * Neither is exercised by any other test: both only matter when a real build runs, which is
 * why each was missing until the app was first run on a device. These assertions keep them
 * from being removed again unnoticed.
 *
 * JavaScript rather than TypeScript because it reads the file system, and the app's TypeScript
 * configuration deliberately carries no Node types.
 */
const { readFileSync } = require('fs');
const path = require('path');

const mobileRoot = path.resolve(__dirname, '../..');
const workspaceRoot = path.resolve(mobileRoot, '../..');

describe('native setup', () => {
  it('reads .env into the Android build', () => {
    const gradle = readFileSync(
      path.join(mobileRoot, 'android/app/build.gradle'),
      'utf8',
    );

    expect(gradle).toContain(
      `apply from: project(':react-native-config').projectDir.getPath() + "/dotenv.gradle"`,
    );
  });

  it('lets Metro resolve dependencies hoisted to the workspace root', () => {
    const config = require('../../metro.config.js');

    expect(config.watchFolders).toContain(workspaceRoot);
    expect(config.resolver.nodeModulesPaths).toContain(
      path.join(workspaceRoot, 'node_modules'),
    );
  });
});
