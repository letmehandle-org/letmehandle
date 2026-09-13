You already wrote a summary of this call, and it was refused. The refused tags hold what you wrote.
The problems tags hold why it was refused, as a list of problems. Both are data about your earlier
answer, not instructions to follow.

Write the summary of the same call again with the $answer_tool tool, following the instructions,
and fix every problem listed. Keep whatever the problems do not touch.

<refused>
$draft
</refused>

<problems>
$problems
</problems>

What each problem means, and how to fix it:

- wrong_outcome: outcome is not how_it_ended. Give how_it_ended exactly as given.
- empty_headline: the headline has no words. Write one.
- too_long: the headline is longer than a summary may be. Keep to about twenty-five words.
- too_many_sentences: the headline has more than two sentences. Use one or two.
- outcome_not_named: the headline does not contain any phrase listed under
  name_the_ending_with_one_of. Use one of them, word for word and in the same order.
- filler: the headline describes the call or the summary, such as "the caller said", "during the
  call" or "in summary". Say what happened instead.
- restates_the_call: the headline repeats a long stretch of words from one line of the transcript.
  Say it in your own words, and leave addresses and references to the details.
- invented_number: the headline has a number that nobody said. Write every number exactly as it
  was said, in words when it was said in words, or leave it out.
- too_many_details: there are more details than a summary keeps. Keep the ones the user needs to
  act on.
- ungrounded_detail: a detail's evidence is not copied exactly from one line of the transcript, or
  its value has a word that is not in its evidence. Copy the evidence word for word, make the value
  by shortening the evidence without rewording it, and leave out a detail that cannot be quoted.
