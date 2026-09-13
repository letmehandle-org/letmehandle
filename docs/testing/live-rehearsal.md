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

For each call it reports the states the run moved through, when the media stream came up, whether
and when the assistant spoke, how soon it replied to each line, the transcript lines stored (a count,
and the share of the scripted words transcribed — never the text), the stored outcome and headline,
the escalation and each push, the diagnostics timeline, and every measurement the process recorded.

## Running it

```
cd apps/backend
uv run python ../../scripts/live_rehearsal.py --env-file ../../.env
uv run python ../../scripts/live_rehearsal.py --env-file ../../.env --only B --log-level info
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

## What still needs a real telephone network

- A carrier forwarding the user's line, and whether it sends `ForwardedFrom` (D-033).
- The provider's real callbacks and signatures at a public URL, and its real conference.
- What each party actually hears: the conference mix, a held participant, the assistant's audio over
  the network's latency and jitter, and talking over the assistant with a real handset's echo.
- A real phone ringing and being answered, which takes seconds rather than milliseconds, and machine
  detection on a real voicemail.
- Push delivered to real devices, locked and unlocked.

All of it is in [`manual-verification.md`](manual-verification.md).
