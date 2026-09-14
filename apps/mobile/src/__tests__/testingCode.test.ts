import { showsTestingCode } from '../screens/VerifyCodeScreen';

// The fixed code is shown only by development builds.
describe('the fixed testing code hint', () => {
  it('is shown in a development build', () => {
    expect(showsTestingCode('development')).toBe(true);
  });

  it.each(['staging', 'production'] as const)('is never shown in %s', name => {
    expect(showsTestingCode(name)).toBe(false);
  });
});
