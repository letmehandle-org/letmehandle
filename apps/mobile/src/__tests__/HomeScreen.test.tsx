import { render } from '@testing-library/react-native';
import React from 'react';

import { initialiseI18n } from '../i18n';
import { en } from '../i18n/locales/en';
import { HomeScreen } from '../screens/HomeScreen';

/** Every string the tree actually renders, read from the output rather than from internals. */
function renderedStrings(node: unknown): string[] {
  if (typeof node === 'string') {
    return [node];
  }
  if (Array.isArray(node)) {
    return node.flatMap(renderedStrings);
  }
  if (node !== null && typeof node === 'object' && 'children' in node) {
    return renderedStrings((node as { children: unknown }).children);
  }
  return [];
}

describe('HomeScreen', () => {
  beforeAll(async () => {
    await initialiseI18n('en');
  });

  it('renders its translated content', async () => {
    const view = await render(<HomeScreen />);
    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    expect(view.getByText(en.home.title)).toBeOnTheScreen();
    expect(view.getByText(en.home.subtitle)).toBeOnTheScreen();
  });

  it('renders no untranslated literal', async () => {
    const view = await render(<HomeScreen />);
    // Every string on the screen must come from the catalogue. A literal added to a component
    // is invisible until somebody adds a second locale and it does not translate.
    const known: string[] = Object.values(en.home);
    const shown = renderedStrings(view.toJSON());

    expect(shown.length).toBeGreaterThan(0);
    for (const text of shown) {
      expect(known).toContain(text);
    }
  });
});
