# SpeechProvider

A spoken conversation: caller audio in, the assistant's voice out, interruption, and context that
can change mid-call. Deliberately unaware of calls — it consumes an audio source and writes to a
sink, and whether those are a phone line, a microphone or a test is not its business (D-005).

Interface: `apps/backend/src/letmehandle/domain/ports/speech.py`
Contract suite: `apps/backend/tests/contracts/speech.py`
Adapters: `adapters/speech/realtime/`, `adapters/speech/elevenlabs/` and
`adapters/speech/gpt_live/`, chosen by `SPEECH_PROVIDER`

## The three adapters

| | `realtime` | `elevenlabs` | `gpt_live` |
| --- | --- | --- | --- |
| Protocol | OpenAI Realtime-compatible websocket | ElevenLabs Agents websocket | OpenAI GPT-Live session websocket |
| Named by | `SPEECH_MODEL` | `SPEECH_AGENT_ID` | `SPEECH_MODEL` |
| Key sent as | `Authorization: Bearer` | `xi-api-key` | `Authorization: Bearer` |
| Barge-in | The service's speech signal; the adapter cancels the response and truncates it to what was heard | The service's interruption event; the adapter drops the agent's audio it was holding | The model's own: it stops when talked over. The caller's start of speech is reported with their first words |
| Context mid-call | Replaces the instructions | Adds background context; the instructions stay | Adds the lines that changed to the instructions |
| Reconnecting | A fresh session with the instructions, voice and recent turns restored | A new conversation whose prompt carries the instructions and recent turns | A new session with the current instructions and recent turns as its history, told not to greet again |
| Greeting | None: the caller speaks first | The locale's greeting, as the conversation's first message | The locale's greeting, which the model is told to say first |
| Language mid-call | The session's language throughout | The agent's language detection switches language, and voice, to the caller's; the client is not told | The adapter tells the model, in the caller's language and as quiet context, to switch when a settled utterance is clearly in another listed language; the voice stays |

All three run behind the same guarantees: the reader never waits on the consumer, only audio is
bounded, every exit path releases the connection and its tasks, and a failure that retrying cannot
fix ends the session with a typed failure rather than a loop.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SPEECH_PROVIDER` | `realtime`, `elevenlabs` or `gpt_live` |
| `SPEECH_ENDPOINT_URL` | The service's websocket URL |
| `SPEECH_MODEL` | The model, for `realtime` and `gpt_live` |
| `SPEECH_AGENT_ID` | The agent, for `elevenlabs` |
| `SPEECH_LANGUAGES` | The languages the service speaks, such as `en,hi`; English by default |
| `SPEECH_API_KEY` | The key. Never logged, never printed, never in an exception |
| `SPEECH_VOICES`, `SPEECH_DEFAULT_VOICE` | The voices the service speaks, as ids it recognises |

None of the connection variables is needed for the service to start: nothing opens a speech
session in a request yet. Whatever does open one fails naming the variable that is missing.

## Preparing an ElevenLabs agent

The adapter configures each conversation through overrides, so the agent has to permit them. In
the agent's security settings:

1. **Allow overrides** for the system prompt, the first message, the language and the TTS voice.
   Without the first-message override no conversation opens: every conversation is sent the
   greeting for its language, and every reconnect an empty first message.
2. **Keep the `user_transcript` and `interruption` client events enabled.** Without the first the
   assistant has no record of what the caller said; without the second it cannot be interrupted.
3. **Set the input and output audio formats on the agent.** The adapter reads what was negotiated
   and converts either way; `ulaw_8000` suits a phone line.
4. **Give the API key access to the Agents platform** for that agent.
5. **List the agent's voices in `SPEECH_VOICES`** using the voice ids ElevenLabs uses.
6. **For every language in `SPEECH_LANGUAGES` beyond the agent's own** (D-039): add the language to
   the agent, give it a language preset with a voice that speaks it natively, and enable the
   `language_detection` system tool. An English agent keeps an English-only TTS model; the
   service gives a preset for another language a multilingual model itself.

A conversation opens in the user's language when `SPEECH_LANGUAGES` lists it. With more than one
language listed the adapter sends no voice: the service holds a client's voice for the whole
conversation, across a switch of language, so the agent's own voice for each language is used and
a voice chosen in the app is not. With one language the resolved voice is sent, as before.

Such an agent is also told, after the instructions, to change language with the
`language_detection` tool without asking the caller: against the real service, a model given the
application's instructions answered a Hindi caller in English and never called the tool until told.

What the service does not offer, found against it: nothing the adapter reads says the agent
switched — the tool call shows only in the conversation's record afterwards — so the application
cannot tell which language the assistant ended a call in. A key on the free plan cannot synthesise
speech in a library voice through the text-to-speech API, although an agent's preset speaks in
one.

The key is sent from the backend, which holds it. It is never sent to a device.

## Using GPT-Live

```
SPEECH_PROVIDER=gpt_live
SPEECH_ENDPOINT_URL=wss://api.openai.com/v1/live/sessions
SPEECH_MODEL=gpt-live-1
SPEECH_LANGUAGES=en,hi
```

`SPEECH_API_KEY` is a project key with access to the model. List the service's voices in
`SPEECH_VOICES` by their API names — `gleam`, `meridian`, `vesper` and the rest — with the locales
each is to speak; the service's voices are described as English or Portuguese, and they speak Hindi
when told to in Hindi, which the adapter does (D-040). Every session holds one of the account's
concurrent sessions for as long as the call lasts, and is billed by the second of voice.

What the adapter derives, because the protocol does not say it: when a turn is over (a second and a
half of quiet on the session's timeline), when the assistant starts and
stops speaking (the loudness of its audio), and which language the caller changed to (the script
their words were written down in). `interrupt` from this side stops playing the rest of the
assistant's current speech; the service cannot be told to stop.

## Adding one

A new protocol is a new adapter; a new service that speaks an existing protocol is only
configuration. A worked example, for a service with a websocket protocol of its own:

1. **The adapter**, in `adapters/speech/<protocol>/`: a `SpeechProvider` whose `connect` returns a
   `SpeechSession`, declaring `SpeechCapabilities` honestly. Reuse
   `adapters/speech/session_support/` for bounded queues, reconnection and timing, as the existing
   adapters do, so the guarantees above hold without being written a third time.
2. **The contract**, in `apps/backend/tests/contracts/test_<protocol>_speech_contract.py`, against a
   scripted service rather than the real one:

   ```python
   class TestExampleSpeechProvider(SpeechProviderContract):
       @pytest.fixture
       def provider(self) -> ExampleSpeechProvider:
           self.service = ScriptedExampleService()
           return ExampleSpeechProvider(
               self.service.open,
               RecordingMetrics(),
               languages=("en",),
               input_formats=(SPEECH_WIDEBAND, TELEPHONY_NARROWBAND),
               output_format=SPEECH_WIDEBAND,
           )
   ```

3. **The settings.** A member of `SpeechProviderName`, and any variable of its own in `Settings`
   with a `description`, `.env.example` and the backend's environment in `docker-compose.yml`;
   `make config-reference` writes the reference and `make verify` fails until they agree.
4. **The choice.** A case in `build_speech_provider` in `bootstrap.py`. The match is exhaustive.

## Verifying without an account

Every behaviour above is exercised in CI against in-process websocket servers that speak each
protocol — `tests/support/simulated_realtime_service.py` and `simulated_elevenlabs_service.py` — so
the real client code runs over a real socket end to end. The GPT-Live adapter's session is tested
against `tests/support/scripted_gpt_live_connection.py`, a scripted service in memory, and its
handshake against a loopback websocket. The simulations require what the real
services require: a pong for every ping, a permitted override, a cancel before audio stops.

A conversation with a live service is a manual step:

```
cd apps/backend
uv sync --group harness
uv run python ../../scripts/speech_harness.py
```

It speaks through the microphone and speaker, accepts `context`, `interrupt`, `disconnect` and
`quit`, and prints a latency summary on exit. Nothing is recorded.
