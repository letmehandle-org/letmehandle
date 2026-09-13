import { render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';

// Jest hoists jest.mock, so the factory refers only to `mock`-prefixed variables.
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
    // Initialisation is held open so the first render happens before translations load.
    let release: () => void = () => undefined;
    mockGate = new Promise<void>(resolve => {
      release = resolve;
    });

    const view = await render(<App />);
    expect(view.toJSON()).toBeNull();

    release();

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
    mockGate = Promise.reject(new Error('catalogue unavailable'));
    const errors = jest
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);

    await expect(render(<App />)).rejects.toThrow('catalogue unavailable');

    errors.mockRestore();
  });

  it('wraps a non-error rejection so that something useful is thrown', async () => {
    mockGate = Promise.reject('the catalogue is missing');
    const errors = jest
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);

    await expect(render(<App />)).rejects.toThrow('the catalogue is missing');

    errors.mockRestore();
  });

  it('does not update state after it has been unmounted', async () => {
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
