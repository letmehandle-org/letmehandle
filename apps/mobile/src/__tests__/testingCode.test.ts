import { showsTestingCode } from '../screens/VerifyCodeScreen';

// FOR TESTING ONLY — delete with showsTestingCode before launch.
describe('the fixed testing code hint', () => {
  it('is shown in a development build', () => {
    expect(showsTestingCode('development')).toBe(true);
  });

  it.each(['staging', 'production'] as const)('is never shown in %s', name => {
    expect(showsTestingCode(name)).toBe(false);
  });
});
