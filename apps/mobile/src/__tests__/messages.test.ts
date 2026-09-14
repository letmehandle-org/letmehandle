/** Which message a failure deserves. */
import { ApiError, NetworkError } from '../api/errors';
import { describeFailure } from '../api/messages';

const t = (key: string): string => key;
const MESSAGES = { refused: 'refused', rateLimited: 'limited' };

function apiError(status: number, code: string): ApiError {
  return new ApiError(status, { error: code, message: 'no' });
}

describe('describing a failure', () => {
  it('reports an unreachable service as such', () => {
    expect(describeFailure(new NetworkError(new Error('x')), t, MESSAGES)).toBe(
      'common.noConnection',
    );
  });

  it('reports a rejected request as something the user can fix', () => {
    expect(describeFailure(apiError(422, 'invalid_request'), t, MESSAGES)).toBe(
      'refused',
    );
  });

  it('reports a refused credential the same way', () => {
    expect(
      describeFailure(apiError(401, 'invalid_credentials'), t, MESSAGES),
    ).toBe('refused');
  });

  it('reports too many attempts where the screen has something to say about it', () => {
    expect(describeFailure(apiError(429, 'rate_limited'), t, MESSAGES)).toBe(
      'limited',
    );
  });

  it('falls back where the screen has no message for a limit', () => {
    expect(
      describeFailure(apiError(429, 'rate_limited'), t, { refused: 'refused' }),
    ).toBe('common.somethingWentWrong');
  });

  it('says nothing specific about a failure inside the service', () => {
    expect(describeFailure(apiError(500, 'internal_error'), t, MESSAGES)).toBe(
      'common.somethingWentWrong',
    );
  });

  it.each([[new Error('anything')], ['a string'], [null], [undefined], [42]])(
    'falls back for %p',
    thrown => {
      expect(describeFailure(thrown, t, MESSAGES)).toBe(
        'common.somethingWentWrong',
      );
    },
  );
});
