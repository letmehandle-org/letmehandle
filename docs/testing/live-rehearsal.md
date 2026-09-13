# Live rehearsal

Whole calls through the real product, on the configured realtime speech service and a real
OpenAI-compatible model, with only the telephone network simulated. It proves what the automated
scenarios cannot — that the assistant a caller would hear, the agent that judges the call and the
summariser that writes its history behave on real services — before a telephony account exists.
What it cannot prove is listed at the end, and is what
[`manual-verification.md`](manual-verification.md) is for.

## What runs

`scripts/live_rehearsal.py` starts the application on loopback the way the end-to-end scenarios do
(`tests/e2e/harness.py`): its own lifespan and orchestrator, storage in a PostgreSQL database created
for the run and dropped after it, and the simulated telephony provider from
`tests/support/simulated_twilio.py` calling it back with signed requests and a real media websocket.
Unlike the scenarios, nothing on the product's side is scripted: no assistant is handed to the
application, so the composition root builds the speech service and the agent from settings, as a
deployment does. Push goes to a recording provider per platform, standing where credentials would.

The signing key, the transcript keys and the diagnostics token are generated for the run. Only
`SPEECH_*` and `LLM_*` are read from the env file, and nothing read from it is printed.

A user signs in through the API with the mock code, allows unknown and withheld callers to the
assistant around the clock, grants taking a message and nothing else, sets the escalation threshold
at 40, and registers a phone on each platform. Then each scenario places a call forwarded from the
user's fictional number:

- the caller's lines are synthesised by the speech service's own text-to-speech, in a voice other
  than the assistant's, as 8 kHz μ-law, and held in memory;
- the caller's side of the media stream sends 20 ms frames at the pace of a phone line, silence
  between lines, and waits for the assistant to finish speaking and pause before its next line;
- the assistant's audio coming back is counted and timed as it arrives, and not kept (D-013).

| Scenario | The caller says | The user's phone |
| --- | --- | --- |
| A, routine | "Hello, I'm calling from the dental clinic to confirm your appointment tomorrow at 10." then "No, that's everything. Thank you, goodbye." and hangs up | keeps ringing |
| B, urgent | "Hi, this is Sam, her neighbour. I need to speak to her right now, it's urgent." then "Water is pouring through her ceiling from the flat upstairs. Please put her on." and, a few seconds after the user joins, hangs up | answers |
| C, Hindi caller, English user | A courier, in Hindi: the parcel arrives tomorrow at ten; then that is all, thank you; and hangs up | keeps ringing |
| D, Hindi caller, Hindi user | A pharmacy, in Hindi: the medicines are ready until six; then that is all; and hangs up | keeps ringing |
| E, urgent Hindi caller, Hindi user | A neighbour, in Hindi: it is very urgent, call her now; then "all right, I'll wait"; and hangs up | keeps ringing |

C, D and E set the user's locale first (`en` and `hi`), and run with `SPEECH_LANGUAGES` from the env
file or, when it has none, `en,hi` (D-039). Their caller's lines are spoken by the agent's own voice
for Hindi where the key may use it, and otherwise by a catalogue voice on the service's multilingual
model. Their hang-up is delivered the way a real one was: the media stream stops at once, and the
callbacks follow two seconds later, the assistant's leg before the caller's. For an agent on the
`elevenlabs` protocol the report adds what the service recorded of the conversation: the language
asked for, whether a voice and a greeting were sent, and the share of the replies' letters that are
Devanagari — never the words.

For each call it reports the states the run moved through, when the media stream came up, whether
and when the assistant spoke, how soon it replied to each line, the transcript lines stored (a count,
and the share of the scripted words transcribed — never the text), the stored outcome and headline,
the escalation and each push, the diagnostics timeline, and every measurement the process recorded.

## Running it

```
cd apps/backend
uv run python ../../scripts/live_rehearsal.py --env-file ../../.env
uv run python ../../scripts/live_rehearsal.py --env-file ../../.env --only B --log-level info
uv run python ../../scripts/live_rehearsal.py --env-file ../../.env --only D
uv run python ../../scripts/live_rehearsal.py --env-file ../../.env --only E
```

It needs a PostgreSQL server it may create and drop a database on (`--database-server`, by default
the development one on port 5433), and a key with text-to-speech as well as agent access. Each run is
two calls of about twenty and forty-five seconds and a handful of model requests; keep runs to the
ones that answer a question.

## Results, 2026-09-13

Seven runs while building it. The figures are from the last, which ran both scenarios on the final
script; where an earlier run showed something different, it is said.

### A: a routine caller

| | |
| --- | --- |
| States | received → routing → agent_handling → escalation_requested → human_ringing → completed, in 22 s |
| Media stream | up 0.06 s after the call arrived |
| The assistant | spoke: greeting 1.0 s after the stream opened, then 4.4 s and 1.7 s of reply; 7.1 s of audio in 5 media messages |
| Replies | 2.07 s after the first line ended, 0.86 s after the second |
| Transcript | 2 lines the caller's, 3 the assistant's; 100 % of the scripted words transcribed |
| Escalation | `action_not_authorised`, delivered; established "The dental clinic is calling to confirm the user's appointment tomorrow at 10:00." |
| Push | one per platform: "The caller wants something only you can allow", "Unknown caller", reason and what was established; no number, no transcript |
| Stored | ended, `unanswered_escalation`, handling assistant, intent appointment, human joined no |
| Headline | "A dental clinic called to confirm your appointment tomorrow at 10:00, but the assistant couldn't confirm it and could not reach you." |

The assistant handled the call and the summary was stored, but it did not treat it as routine: in
every run of A the agent read "to confirm your appointment" as asking for a confirmation only the
user may give, and rang the user. See finding 2.

### B: a caller who urgently needs the user

| | |
| --- | --- |
| States | received → routing → agent_handling → escalation_requested → human_ringing → human_joined → completed |
| Media stream | up 0.04 s after the call arrived |
| The assistant | spoke: greeting 1.0 s after the stream opened, then a 6.6 s reply; 7.5 s of audio in 6 media messages |
| Replies | 1.86 s after the first line; none after the second, said once the user had joined |
| Ring | escalation requested 8.4 s after the caller finished asking; the user's leg dialled 8 ms later and joined 10 ms after that (the simulated phone answers at once) |
| Transcript | 1 line the caller's, 2 the assistant's; the first line fully transcribed, the second said after the assistant had left the conversation |
| Escalation | `caller_asked_for_the_user`, delivered, ended; established "A caller identifying as "Sam, her neighbour" wants to speak to the user immediately and calls it urgent, but has not said what it concerns." |
| Push | one per platform: "The caller asked for you", with that context |
| Stored | ended, `handed_to_user`, human joined yes |
| Headline | "Caller claiming to be the user's neighbour "Sam" asked for the user urgently, without saying what about." |

The agent both escalated and asked to end the call as handed over, so, as designed, the assistant
stayed with the caller while the phone rang and left the conversation the moment the user joined.
The caller's second line went to the user, not the assistant, which is why it is not in the
transcript.

### Latency, from the process's own measurements

| Measure | p50 | max |
| --- | --- | --- |
| Speech service session opening (`call.speech_open_seconds`) | 0.60 s | 0.63 s |
| Speech service time to first audio (`speech.time_to_first_audio_seconds`) | 0.41 s | 0.41 s |
| Assistant's first audio on the line, from the stream opening | 1.0 s | 1.04 s |
| Reply on the line after a caller's line ended, measured by the script | 1.86 s | 2.07 s |
| One agent judgement on the model (`call.judgement_seconds`) | 6.85 s | 8.83 s |
| Summary written by the model (`call.summary_seconds`) | 1.27 s | 1.45 s |
| Dial, answer and terminate requests to the simulated provider | < 1 ms | 1 ms |

Across the runs whose measurements were read, judgements took 6.7–8.8 s, first audio 0.9–1.2 s
after the stream opened, and replies 0.9–2.5 s. No circuit opened, and nothing failed: no
`call.judgement_failed`, `call.summary_failed`, `call.speech_unavailable` or
`call.provider_failed`.

## Results, 2026-09-13: Hindi

On an agent prepared as `docs/providers/speech.md` describes — English, with a Hindi language
preset and its language detection tool — and the same model. The key's monthly speech allowance ran
out part-way, so this is one rehearsed call and a handful of conversations opened on the service
directly with the overrides the adapter sends, rather than every scenario run twice.

### C: a Hindi caller rings an English-speaking user (one run)

| | |
| --- | --- |
| States | agent_handling → completed, in 42 s |
| Hang-up | media stream stopped, callbacks two seconds later with the assistant's leg first |
| Stored | ended, `caller_hung_up`, intent delivery_in_progress |
| Headline | "A courier company called to say your parcel will arrive tomorrow morning, then hung up." — English, the user's locale |
| The service's record | language asked for `en`, no voice sent, greeting sent; 2 replies, 0 % Devanagari |
| Replies | none to the first line; 2.5 s after the second |

The call was stored as the hang-up it was: the defect below did not recur. But the assistant never
changed language. It greeted in English, the service wrote the caller's Hindi down in Latin script,
and the model answered in English. This run predates the adapter telling the agent to change
language; see finding 8.

### The service on its own

Conversations opened directly, each with an English opening, an English greeting and two Hindi
lines from the caller:

| Instructions and voice sent | What happened |
| --- | --- |
| The application's instructions, no voice | Replies in English; the language tool never called |
| The same, told to change language "using your tool if you have one" | Replies in English; never called |
| The same, told which languages and to call `language_detection` (what the adapter now sends) | Tool called after the first line; every later reply in Hindi, and the caller's next line written in Devanagari |
| The service's own instructions, a client voice sent | Asked "Would you like to continue in Hindi?", switched once the caller said yes; replies in Hindi |

A voice sent by the client held through that switch. Measured by the median pitch of the replies,
a conversation opened with a clearly higher voice went on at that voice's pitch in Hindi rather
than the Hindi preset voice's. Pitch at 8 kHz is a coarse measure, which is why the adapter's
choice rests on it only for multilingual agents, where sending a voice buys nothing.

### Summaries in Hindi, on the real model

The summariser, given ended calls built in memory, for a user whose locale is `hi` unless said:

| Call | Summaries | Kept as |
| --- | --- | --- |
| A courier in Hindi, caller hung up | 3 | 3 first drafts; every headline Devanagari, ending named with "फ़ोन रख दिया" |
| A pharmacy in Hindi, handled by the assistant | 4 | 2 first drafts, 1 corrected draft, 1 fallback (refused twice as restating the call) |
| A dental clinic in English | 1 | first draft, headline in Hindi, details quoted in English |
| The courier in Hindi, for an English user | 1 | first draft, headline in English, details quoted in Hindi |

Each took 1.4–3.2 s. Details quoted from Hindi lines passed the checks, which now read Devanagari
words whole.

## Results, 2026-09-14: an urgent caller while the user is being reached

On a real call a Hindi-speaking caller said it was very urgent and asked for the user to be called.
The product decided to escalate five seconds later and dialled a second after that, but the
assistant had already answered that it could not call the user, and only said it was trying to
reach them on its reply after that. Two things let it: its instructions said nothing about being
asked for the user until it was told the user was being reached, and it was told only once the
dial had been accepted. Both changed (finding 11); E rehearses that call.

### E: an urgent Hindi caller, and a user who does not answer (one run)

| | |
| --- | --- |
| States | agent_handling → escalation_requested → human_ringing → completed, in 39 s |
| Judgement | 8.2 s; escalation requested 8.2 s after the caller's line settled, dialled 7 ms later |
| Replies | 3.5 s after the urgent line, 1.3 s after the second |
| Stored | ended, `unanswered_escalation`, intent personal, human joined no |
| Escalation | `caller_asked_for_the_user`, delivered, ended |
| The service's record | language asked for `hi`, no voice sent, greeting sent; 5 replies, 94 % Devanagari |

The stored transcript, read for this run only, in English:

| Settled | Who | What was said |
| --- | --- | --- |
| 0.0 s | Caller | Hello, I'm Sam, their neighbour. It's very urgent. Please phone them now. |
| 6.2 s | Assistant | Hello Sam, I'll check whether they can take the call right now. Please wait. |
| 8.2 s | | *escalation requested; the assistant told the user is being reached; the user dialled* |
| 10.9 s | Caller | All right, I'll wait. |
| 15.3 s | Assistant | Thank you. I'm checking now whether they can take the call. |

The first reply was written before the judgement had finished, so it rests on the instructions
alone: asked for the user, the assistant offered to check rather than refusing. The second rests on
being told, and held the same line without promising the user would come.

## Findings

1. **Fixed — an unanswered escalation was stored as handed to the user.** In the first run of A the
   agent recorded the outcome `handed_to_user`, with its own headline, while the user's phone was
   ringing; the caller hung up before it was answered, and that record replaced the unanswered
   escalation in history (`human_joined` false, outcome `handed_to_user`). The summariser was
   already held to the outcome the facts establish; the agent's record was not. It now is: a
   record naming a different ending is left out and the checked summary kept
   (`fix(summary): keep the agent's record only for the ending the call had`, with a test in
   `tests/unit/application/orchestration/test_assistant_calls.py`). Every later run of A stored
   `unanswered_escalation`.
2. **Open — an appointment confirmation rings the user.** With nothing granted, and again with
   taking a message granted, the agent escalated A as `action_not_authorised` (the capability to
   confirm appointments). The evaluation suite's routine dental call says "No need to call back" and
   expects no escalation; a caller who only says they are confirming is read as asking for a
   confirmation. Whether that is right is a product decision, and a prompt change goes through
   `scripts/agent_evaluation.py`, so nothing was changed here.
3. **Open — the reason given once was not the real one.** In one of six runs of B the model
   assessed an articulate caller asking for the user as not understood ("hasn't said what it's
   about"), and the push read "The caller cannot be understood". The ring still happened. Every other
   run gave `caller_asked_for_the_user`.
4. **Open — a checked summary can still misattribute.** One run's headline for A said the clinic
   "didn't answer when we tried to pass the message on": the user was who did not answer. It passed
   the summary checks, which require the ending to be named and nothing invented, not who it
   happened to.
5. **Measured — escalation is as fast as one judgement.** From the caller finishing "I need to speak
   to her right now" to the user's phone ringing took 8.4 s, nearly all of it the model's judgement.
   Everything the product does around it takes milliseconds.
6. **Observed — the service's audio arrives in stretches of about a second**, and the transport
   paces it onto the line as it comes, so a reply of seven seconds is five or six media messages
   rather than 350 frames. Nothing misbehaved; it is one of the frame facts D-027 leaves to the first
   real call.

7. **Fixed — a caller hanging up was stored as a failed call.** On the first call over a real
   telephone network the media stream closed as the caller hung up and the provider reported the
   assistant's leg leaving before the caller's own leave, about two seconds later; the assistant
   leaving was taken as the assistant lost. It now waits `speaker_gone` for the ending
   (`fix(calls): a hang-up heard of after the assistant's leg is a hang-up`, reproduced in
   `tests/e2e/test_provider_faults.py`). Scenario C delivers its hang-up that way and was stored
   `caller_hung_up`.
8. **Fixed, not yet rehearsed — the assistant did not follow a Hindi caller.** The agent changes
   language only when its model calls the language tool, and the application's instructions alone
   never made it (see the service on its own, above). An agent listed for several languages is now
   told to. Rehearsing C and D again, with it, is what the next allowance is for.
9. **Open — Hindi summaries fall back more often for restating the call.** One of eight Hindi
   summaries fell back, and another needed its correction, both for a nine-word run copied from a
   line. Hindi spends more words than English on the same phrase (postpositions and auxiliaries are
   words of their own), so the same run is a shorter quotation. A per-language length is a change
   to measure with more calls than these, not to guess.
10. **Limits of the service.** A key on the free plan cannot speak through the text-to-speech API in
    a library voice (HTTP 402), though an agent's language preset may use one; so the caller's
    Hindi was spoken by a catalogue voice on the multilingual model. Nothing the adapter reads tells
    it the agent changed language. And the free allowance covers only a few rehearsed calls a
    month.

11. **Fixed — the assistant said it could not call the user while their phone was being dialled.**
    The assistant's instructions now forbid saying or suggesting it cannot reach the user, and tell
    it, when asked for them, to say it will check whether they can take the call, without promising
    (`fix(agent): the speaking assistant offers to check, never says it cannot`). The run tells it
    the user is being reached before it asks for the dial rather than after, and that it failed if
    the dial is refused (`fix(orchestration): tell the assistant the user is being reached before
    dialling`). The update is sent as background the model reads before its next reply, not as words
    of the caller's, which it is told to discount. A judgement takes six to nine seconds and a reply
    two or three, so the reply to the line that asks is nearly always written before anything can
    be told: the instructions are what that reply rests on, and E shows them holding.

## What still needs a real telephone network

- A carrier forwarding the user's line, and whether it sends `ForwardedFrom` (D-033).
- The provider's real callbacks and signatures at a public URL, and its real conference.
- What each party actually hears: the conference mix, a held participant, the assistant's audio over
  the network's latency and jitter, and talking over the assistant with a real handset's echo.
- A real phone ringing and being answered, which takes seconds rather than milliseconds, and machine
  detection on a real voicemail.
- Push delivered to real devices, locked and unlocked.

All of it is in [`manual-verification.md`](manual-verification.md).
