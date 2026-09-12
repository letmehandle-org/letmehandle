const path = require('path');
const { getDefaultConfig, mergeConfig } = require('@react-native/metro-config');

// This is a pnpm workspace with node-linker=hoisted (see /.npmrc), so the app's dependencies
// are installed in the workspace root rather than under apps/mobile. Metro only resolves files
// inside the folders it watches, and by default that is apps/mobile alone — so without the
// root here it cannot find react-native itself.
const workspaceRoot = path.resolve(__dirname, '../..');

/**
 * Metro configuration
 * https://reactnative.dev/docs/metro
 *
 * @type {import('@react-native/metro-config').MetroConfig}
 */
const config = {
  watchFolders: [workspaceRoot],
  resolver: {
    nodeModulesPaths: [
      path.resolve(__dirname, 'node_modules'),
      path.resolve(workspaceRoot, 'node_modules'),
    ],
  },
};

module.exports = mergeConfig(getDefaultConfig(__dirname), config);
