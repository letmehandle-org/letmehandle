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
| `SPEECH_API_KEY` | The key. Never logged, never printed, never in an exception |
| `SPEECH_VOICES`, `SPEECH_DEFAULT_VOICE` | The voices the service speaks, as ids it recognises |

None of the connection variables is needed for the service to start: nothing opens a speech
session in a request yet. Whatever does open one fails naming the variable that is missing.

## Preparing an ElevenLabs agent

The adapter configures each conversation through overrides, so the agent has to permit them. In
the agent's security settings:

1. **Allow overrides** for the system prompt, the first message, the language and the TTS voice.
   Without the first-message override every reconnect is refused, and the call ends.
2. **Keep the `user_transcript` and `interruption` client events enabled.** Without the first the
   assistant has no record of what the caller said; without the second it cannot be interrupted.
3. **Set the input and output audio formats on the agent.** The adapter reads what was negotiated
   and converts either way; `ulaw_8000` suits a phone line.
4. **Give the API key access to the Agents platform** for that agent.
5. **List the agent's voices in `SPEECH_VOICES`** using the voice ids ElevenLabs uses.

The key is sent from the backend, which holds it. It is never sent to a device.

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
