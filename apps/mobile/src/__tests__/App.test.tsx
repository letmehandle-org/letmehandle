import { render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';

// Jest hoists jest.mock above the imports and rejects references to variables that are not
// prefixed with "mock", because anything else would not be initialised by then.
let mockGate: Promise<void> | undefined;

jest.mock('../i18n', () => {
  const actual = jest.requireActual('../i18n');
  return {
    ...actual,
    initialiseI18n: async (locale?: string) => {
      await mockGate;
      return actual.initialiseI18n(locale);
    },
  };
});

describe('App', () => {
  afterEach(() => {
    mockGate = undefined;
  });

  it('renders nothing until translation is ready, then renders the application', async () => {
    // Rendering before i18next has loaded shows every screen its own keys for a frame. It
    // looks like a flicker in development and like a defect in a release. Initialisation is
    // held open here because otherwise it resolves before the first render is flushed and the
    // assertion would prove nothing.
    let release: () => void = () => undefined;
    mockGate = new Promise<void>(resolve => {
      release = resolve;
    });

    const view = await render(<App />);
    expect(view.toJSON()).toBeNull();

    release();

    // Welcome, not Home: nobody is signed in, and the navigator follows the session rather
    // than starting somewhere and correcting itself.
    await waitFor(() => {
      expect(view.getByTestId('welcome-screen')).toBeOnTheScreen();
    });
    expect(view.getByText(en.welcome.start)).toBeOnTheScreen();
    // The promise reads as one sentence to a screen reader, however it is drawn.
    expect(view.getByRole('header')).toHaveAccessibleName('Let me handle it.');
    for (const promise of Object.values(en.welcome.promises)) {
      expect(view.getByText(promise)).toBeOnTheScreen();
    }
  });

  it('surfaces a translation failure rather than staying blank', async () => {
    // A silent permanent blank screen hides the reason. This asserts the failure is loud.
    mockGate = Promise.reject(new Error('catalogue unavailable'));
    const errors = jest
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);

    await expect(render(<App />)).rejects.toThrow('catalogue unavailable');

    errors.mockRestore();
  });

  it('wraps a non-error rejection so that something useful is thrown', async () => {
    // A promise can reject with anything. Rethrowing a bare string produces an error with no
    // stack and no message, which is worse than the original failure.
    mockGate = Promise.reject('the catalogue is missing');
    const errors = jest
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);

    await expect(render(<App />)).rejects.toThrow('the catalogue is missing');

    errors.mockRestore();
  });

  it('does not update state after it has been unmounted', async () => {
    // The promise can settle after the component has gone. Without the guard this warns in
    // development and, in a longer-lived component, keeps a dead tree alive.
    let release: () => void = () => undefined;
    mockGate = new Promise<void>(resolve => {
      release = resolve;
    });
    const errors = jest
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);

    const view = await render(<App />);
    await view.unmount();

    release();
    await new Promise<void>(resolve => {
      setImmediate(resolve);
    });

    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });
});
