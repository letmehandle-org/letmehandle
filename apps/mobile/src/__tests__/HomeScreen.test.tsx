import { fireEvent, render } from '@testing-library/react-native';
import React from 'react';

import { initialiseI18n } from '../i18n';
import { en } from '../i18n/locales/en';
import { ActivityScreen } from '../screens/ActivityScreen';
import { HomeScreen } from '../screens/HomeScreen';

jest.mock('../preferences/PreferencesProvider', () => ({
  usePreferences: () => ({
    preferences: jest.requireActual('./support/backend').DEFAULT_PREFERENCES,
  }),
}));

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

describe('Home', () => {
  beforeAll(async () => {
    await initialiseI18n('en');
  });

  it('says plainly that nothing is being answered yet', async () => {
    const view = await render(<HomeScreen />);

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.notYet,
    );
    expect(view.getByText(en.home.callsToday)).toBeOnTheScreen();
    expect(view.getByText(en.home.fillsRing)).toBeOnTheScreen();
    expect(view.getByText(en.home.ringsYou)).toBeOnTheScreen();
  });

  it('never claims to be on duty before the assistant takes calls', async () => {
    // Call handling arrives with phases 7 and 8. A zero beside "on duty" would describe an
    // assistant that is not there.
    const view = await render(<HomeScreen />);
    const strings = renderedStrings(view.toJSON()).join(' ');
    expect(strings).not.toMatch(/on duty|answering calls now|handled today/i);
  });
});

describe('Activity', () => {
  beforeAll(async () => {
    await initialiseI18n('en');
  });

  it('is a designed empty state rather than a blank screen', async () => {
    const view = await render(<ActivityScreen />);
    expect(view.getByText(en.activity.title)).toBeOnTheScreen();
    expect(view.getByText(en.activity.empty)).toBeOnTheScreen();
  });
});

describe('the tabs', () => {
  beforeAll(async () => {
    await initialiseI18n('en');
  });

  it('switch between home, activity and settings and mark the one that is open', async () => {
    const { MainTabs } = jest.requireActual('../screens/MainTabs');
    const view = await render(<MainTabs onOpenSetting={() => undefined} />);

    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
    expect(view.getByTestId('tab-home')).toBeSelected();

    await fireEvent.press(view.getByTestId('tab-activity'));
    expect(view.getByTestId('activity-screen')).toBeOnTheScreen();
    expect(view.getByTestId('tab-activity')).toBeSelected();
    expect(view.queryByTestId('home-screen')).toBeNull();

    await fireEvent.press(view.getByTestId('tab-settings'));
    expect(view.getByTestId('settings-screen')).toBeOnTheScreen();
  });
});
