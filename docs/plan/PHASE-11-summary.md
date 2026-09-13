# Phase 11 — Call summary and history

**Goal:** after a call, the user learns what happened in seconds.

## In scope

### The summary
Produced for every assistant-handled call, as a structured object rather than prose with
fields parsed back out of it:

- What the call was about, in one or two sentences a person would actually say.
- Intent and outcome, as domain enumerations.
- Extracted details that matter and are actionable: times, names, reference numbers,
  addresses, amounts, commitments made or declined.
- Whether a human joined, and when.
- Timestamps and durations: received, answered, escalated, human joined, ended.
- A transcript reference while retention still holds it (D-014); the summary itself outlives
  the transcript and stands alone once the transcript is purged.

Generation is deterministic in structure and validated on the way out. A failed generation
produces a summary built from the facts the orchestrator already knows — participants,
timings, outcome — rather than nothing at all.

### Quality
A summary that restates the transcript is a failure of this phase. The tests assert the
properties that make it useful: it is shorter than a threshold, it names the outcome, it
carries extracted details when the call contained them, and it does not include filler.

An evaluation set of representative calls with expected extractions, scored and reported,
so that a prompt change that degrades summaries is caught.

### Backend
- `GET /v1/calls` — paginated, filterable by outcome, date and whether a human joined.
- `GET /v1/calls/{id}` — full detail.
- `GET /v1/calls/{id}/transcript` — available only while retained, and a clear response
  distinguishing "purged" from "never existed".
- Deletion of an individual call by the user, including its transcript, immediately.

### Mobile
- Activity list and call detail from Phase 9, now populated with real summaries.
- The transcript, where present, shown as a secondary surface rather than the headline.
- A clear statement of retention wherever a transcript is shown, so the user knows it will
  go.

## Explicitly out of scope

- Search across calls.
- Export.
- Analytics or trends across calls.
- Editing a summary.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Summary structure validation; the fallback summary when generation fails; extraction of each supported detail type; length and content properties. |
| Unit | A summary remains complete and renderable after its transcript is purged. |
| Integration | Summary written at call completion for every terminal path, including failure and unanswered escalation; listing, filtering and pagination; transcript endpoint distinguishes purged from absent; deletion removes both summary and transcript. |
| Isolation | The Phase 2 helper applied to calls. |
| Evaluation | The summary evaluation set scores at or above its threshold, recorded in the report. |
| E2E | Complete a call, open the app, read the summary; wait past retention, the summary remains and the transcript is gone. |

## Acceptance criteria

1. Every assistant-handled call produces a summary, including calls that failed.
2. Summaries are concise and state the outcome without restating the conversation.
3. Intent, outcome, human participation and timings are present and correct.
4. Extracted details are captured when present and absent when not invented.
5. A summary survives transcript purging and remains useful.
6. History is listable, filterable and paginated, scoped to the user.
7. A user can delete a call and its transcript, immediately and completely.
8. The evaluation set meets its threshold.
9. Coverage meets the D-020 floors.

## Risks and open questions

- **Fabricated details.** A summary that invents a reference number is worse than one that
  omits it. Extraction is validated against the transcript at generation time, and the
  evaluation set includes calls where the tempting detail is absent.
- **Summaries as the only record.** Once the transcript is purged the summary is all there
  is. That raises the bar for the evaluation threshold and is the reason the fallback summary
  exists.

## Verification report

```
PHASE 11 VERIFICATION

Planned tasks:        backend complete; mobile history screens pending
Acceptance criteria:  1–9 passed for the backend
Unit tests:           passed   summary checks, summariser fallbacks, prompts, schema
Integration tests:    passed   the real SDK loop on a scripted model; summaries sealed in PostgreSQL;
                               history listing, filtering, pagination, transcript purged vs absent,
                               deletion
Evaluation:           passed   tests/evaluation/summaries.json and scripts/summary_evaluation.py
                               against a real OpenAI-compatible model, prompts v2, three full runs:
                               every class at or above 90% on the mean, below; degenerate
                               strategies proven to fail every class
Coverage:             100.00%  backend
Docs updated:         this plan
Known issues:         mobile activity and call detail screens are not yet built
```

A model's draft is kept only when every detail quotes the transcript, the outcome agrees with the
call's facts, and the headline is short and names the ending; anything else falls back to the
summary built from the facts. The summariser runs inside teardown's summary bound.

## Evaluation

Run against a real OpenAI-compatible model from `apps/backend`:
`uv run python ../../scripts/summary_evaluation.py`. Three full runs of the summary prompts as
shipped (v2), each class as the mean of the three with the lowest and highest run:

| Class | Calls | Mean | Min | Max |
| --- | --- | --- | --- | --- |
| extraction | 4 | 92% (11 of 12) | 75% | 100% |
| absent_detail | 4 | 92% (11 of 12) | 75% | 100% |
| ending | 4 | 92% (11 of 12) | 75% | 100% |
| no_details | 3 | 100% (9 of 9) | 100% | 100% |

The v1 prompts, on the same model, scored 9 of 15 in one run: extraction 1/4, absent_detail 3/4,
ending 2/4, no_details 3/3.

No check and no expectation was changed. Most v1 failures were drafts the checks refused for
reasons the prompt never explained, so v2 explains them:

- **Restating the call.** Headlines copied a clause of what was said and were refused. v2 asks for
  about twenty-five words in the summary's own words, and leaves addresses and references to the
  details, which is where a user looks for them.
- **Reworded detail values.** "They cannot come" for "I can't come out" is refused as ungrounded,
  and one ungrounded detail refuses the whole draft. v2 says a value shortens its evidence and never
  rewords it, with an example of each.
- **Numbers.** A spoken amount written as digits is a number nobody said. v2 says a number said in
  words stays in words.
- **Intent.** v1 gave intents no meaning, so a failed call became undetermined. v2 defines each and
  asks for what the caller called for, however the call ended.
- **Commitments.** A refusal to come out today went unrecorded. v2 says a commitment counts in
  either direction and from anybody on the call, and that a request is not one.

The misses in three runs were one of each kind still seen while tuning: a neighbour's call read as
an enquiry rather than personal, a headline refused for repeating a line of the call, and a
plumber's "can't come out today" left out of the details. A single run can report a class of four
at 75%, so the 90% threshold holds on the mean of several runs rather than on one.
