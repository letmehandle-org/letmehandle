/** What is wrong with a draft before it is sent, as a translation key; the server still decides. */

/** The longest a topic may be before it stops being a phrase. */
const MAX_TOPIC = 60;

/** A topic with its spacing and case settled, so that two spellings are one topic. */
export function normaliseTopic(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(' ').toLowerCase();
}

export function topicProblem(
  raw: string,
  existing: readonly string[],
): string | null {
  const topic = normaliseTopic(raw);
  if (topic === '' || topic.length > MAX_TOPIC) {
    return 'preferences.personality.invalidTopic';
  }
  return existing.includes(topic)
    ? 'preferences.personality.duplicateTopic'
    : null;
}
