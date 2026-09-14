/** The checks that tell the user about a draft topic before it is saved. */
import { normaliseTopic, topicProblem } from '../preferences/validation';

describe('topics', () => {
  it('folds spacing and case together, so one topic is not three', () => {
    expect(normaliseTopic('  School   Run ')).toBe('school run');
  });

  it('accepts a phrase', () => {
    expect(topicProblem('School Run', [])).toBeNull();
  });

  it('refuses an empty one', () => {
    expect(topicProblem('   ', [])).toBe(
      'preferences.personality.invalidTopic',
    );
  });

  it('refuses a sentence, which is an instruction in a list the agent reads', () => {
    expect(topicProblem('a'.repeat(61), [])).toBe(
      'preferences.personality.invalidTopic',
    );
  });

  it('refuses one already on the list under a different spelling', () => {
    expect(topicProblem('School Run', ['school run'])).toBe(
      'preferences.personality.duplicateTopic',
    );
  });
});
