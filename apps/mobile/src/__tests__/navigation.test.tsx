import { render } from '@testing-library/react-native';
import React from 'react';

import { initialiseI18n } from '../i18n';
import { RootNavigator } from '../navigation/RootNavigator';
import { ROUTES } from '../navigation/routes';

describe('RootNavigator', () => {
  beforeAll(async () => {
    await initialiseI18n('en');
  });

  it('starts on Home', async () => {
    const view = await render(<RootNavigator />);
    expect(await view.findByTestId('home-screen')).toBeOnTheScreen();
  });

  it('names every route in the typed map', () => {
    // The map and the parameter list are kept in step by `satisfies`, so this asserts the
    // names themselves rather than the type, which the compiler has already checked.
    expect(Object.values(ROUTES)).toEqual(['Home']);
  });
});
