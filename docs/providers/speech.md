# SpeechProvider

A spoken conversation: caller audio in, the assistant's voice out, interruption, and context that
can change mid-call. Deliberately unaware of calls — it consumes an audio source and writes to a
sink, and whether those are a phone line, a microphone or a test is not its business (D-005).

Interface: `apps/backend/src/letmehandle/domain/ports/speech.py`
Contract suite: `apps/backend/tests/contracts/speech.py`
Adapters: `adapters/speech/realtime/` and `adapters/speech/elevenlabs/`, chosen by `SPEECH_PROVIDER`

## The two adapters

| | `realtime` | `elevenlabs` |
| --- | --- | --- |
| Protocol | OpenAI Realtime-compatible websocket | ElevenLabs Agents websocket |
| Named by | `SPEECH_MODEL` | `SPEECH_AGENT_ID` |
| Key sent as | `Authorization: Bearer` | `xi-api-key` |
| Barge-in | The service's speech signal; the adapter cancels the response and truncates it to what was heard | The service's interruption event; the adapter drops the agent's audio it was holding |
| Context mid-call | Replaces the instructions | Adds background context; the instructions stay |
| Reconnecting | A fresh session with the instructions, voice and recent turns restored | A new conversation whose prompt carries the instructions and recent turns |
| Greeting | None: the caller speaks first | The locale's greeting, as the conversation's first message |
| Language mid-call | The session's language throughout | The agent's language detection switches language, and voice, to the caller's; the client is not told |

Both run behind the same guarantees: the reader never waits on the consumer, only audio is bounded,
every exit path releases the connection and its tasks, and a failure that retrying cannot fix ends
the session with a typed failure rather than a loop.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SPEECH_PROVIDER` | `realtime` or `elevenlabs` |
| `SPEECH_ENDPOINT_URL` | The service's websocket URL |
| `SPEECH_MODEL` | The model, for `realtime` |
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

What the service does not offer, found against it: no client event when language detection
switches, and no record of the switch among the conversation's tool calls; so the application
cannot tell which language the assistant ended a call in. A key on the free plan cannot synthesise
speech in a library voice through the text-to-speech API, although an agent's preset speaks in
one.

The key is sent from the backend, which holds it. It is never sent to a device.

## Adding one

A new protocol is a new adapter; a new service that speaks an existing protocol is only
configuration. A worked example, for a service with a websocket protocol of its own:

1. **The adapter**, in `adapters/speech/<protocol>/`: a `SpeechProvider` whose `connect` returns a
   `SpeechSession`, declaring `SpeechCapabilities` honestly. Reuse
   `adapters/speech/session_support/` for bounded queues, reconnection and timing, as both existing
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
the real client code runs over a real socket end to end. The simulations require what the real
services require: a pong for every ping, a permitted override, a cancel before audio stops.

A conversation with a live service is a manual step:

```
cd apps/backend
uv sync --group harness
uv run python ../../scripts/speech_harness.py
```

It speaks through the microphone and speaker, accepts `context`, `interrupt`, `disconnect` and
`quit`, and prints a latency summary on exit. Nothing is recorded.
