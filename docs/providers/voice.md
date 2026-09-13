# VoiceProvider

How the assistant sounds. Separate from `SpeechProvider` on purpose: the system that holds the
conversation and the system that supplies a voice are not the same concern, and either can be
replaced without the other.

Interface: `apps/backend/src/letmehandle/domain/ports/voice.py`
Contract suite: `VoiceProviderContract` in `apps/backend/tests/contracts/other_ports.py`
First implementation: `adapters/voice/builtin.py`

## Capabilities

| Capability | Means | What follows from it |
| --- | --- | --- |
| `builtin_voices` | The provider has a fixed catalogue | The selection list is drawn from `list_voices` |
| `preview` | It can return a sample of a voice | `GET /v1/voices/{id}/preview` is registered, and the client draws a play control |
| `custom_voice` | A user can supply a voice it does not ship | The custom-voice surface below exists |
| `cloning` | It can train a voice from a user's recordings | The training flow below exists |
| `local_inference` | It runs on the deployment's own hardware | Audio need not leave the deployment to be spoken |
| `realtime_streaming` | It can synthesise as the sentence is produced | The agent may speak before it has finished deciding what to say |

Everything defaults to false. A provider that forgets to declare something offers less than it
could, which is recoverable; one that inherits a `true` it did not mean offers a feature that
fails in front of a caller.

Capabilities decide the API surface, not only the interface — see D-024. A provider that does
not declare `preview` leaves the application with no preview route at all, so the path is absent
from the OpenAPI schema and from the generated client.

## What the built-in provider declares

`BuiltInVoiceProvider` is a catalogue supplied through its constructor, with a default. It
declares `builtin_voices`, and `preview` only when it was given sample audio.

`cloning` and `custom_voice` are false permanently rather than pending. Nothing in this product
can train a voice, and a `true` there renders a training flow in front of somebody it will fail
for — which is the outcome D-009 exists to prevent.

The project ships no catalogue of its own. A compatible speech service decides which voices exist
(D-008), so the application builds the provider from configuration: `SPEECH_VOICES` lists the
voices as `id:Display name:locale|locale`, comma-separated, and `SPEECH_DEFAULT_VOICE` names one of
them. Both are required — the process refuses to start without them, naming the variable — and a
malformed entry, a repeated id or a default outside the list is refused the same way.
`.env.example`, the development compose file and the test settings carry example values that say
they are examples, so that the application starts; no service speaks them.

Configuration names no sample audio, so the application builds the provider without any. It
therefore offers a catalogue, a default and no preview control anywhere. That is the capability
model working rather than a gap in it.

## Resolution

One chain, implemented once, in `resolve_voice`:

```
the user's cloned voice → the persona voice they chose → the provider's default
```

Each step falls through when the voice is not available, so a revoked or broken voice produces a
call that sounds different rather than a call that does not happen. Silence is the one outcome
this must never produce, which is why every provider is required to have a default and why the
contract suite asserts the chain at each step.

`GET /v1/preferences/voice` returns `resolved_voice_id` alongside the choice. They differ exactly
when something has gone wrong with a voice, and that is the moment a user should be able to see
it.

## Preview

`preview(voice_id)` returns audio and the media type it is in. The format travels with the bytes
because a caller that has to guess produces a response the client cannot play, and the guess is
wrong the first time a provider returns anything but the format that was assumed.

It raises `CapabilityNotSupportedError` when the provider does not declare `preview`, and a
domain error when the voice is unknown — never a `KeyError` and never a vendor exception. A
caller must be able to tell "this provider cannot do that" from "there is no such voice", and
neither is a server fault.

A sample is the same bytes for every user and never changes for a given voice, so the response is
cached at the client for a day. `Cache-Control` is `private`: which voice somebody is listening
to is a thing about them. A provider that synthesises samples on demand rather than shipping them
should cache them itself, keyed by provider, voice and locale, so that changing provider cannot
serve a stale sample.

## Adding one

A worked example: a provider that ships sample audio for its voices, and so declares `preview`.

```python
class SampledVoiceProvider(VoiceProvider):
    """A fixed catalogue with a recorded sample of each voice."""

    @property
    def capabilities(self) -> VoiceCapabilities:
        return VoiceCapabilities(builtin_voices=True, preview=True)
```

Prove it with the contract, which checks that `preview` answers exactly when it is declared and that
resolution always reaches a voice:

```python
class TestSampledVoiceProvider(VoiceProviderContract):
    @pytest.fixture
    def voices(self) -> SampledVoiceProvider:
        return SampledVoiceProvider(catalogue=EXAMPLE_VOICES, samples=synthetic_samples())
```

Samples in tests are generated in memory; no audio file is ever committed (D-013). Construct it in
`build_voice_provider` in `bootstrap.py`. Because it declares `preview`, the preview route appears in
the application, the OpenAPI schema and the generated client, and `make api-types` records that.

## The custom-voice surface, specified and not built

No provider in this product declares `cloning` or `custom_voice`, so none of the following
exists in the running application. It is specified here so that the first provider that declares
them is implemented against a design rather than around one, and so that Phase 12's privacy
review has something to review.

A voice recorded from a person is biometric data in several jurisdictions. The design therefore
starts from what must be true rather than from what would be convenient.

1. **Consent is explicit, specific and revocable.** A separate step, stating what is recorded,
   where it is processed, how long it is kept and how to delete it. Not bundled into the terms,
   not implied by tapping Record. Refusing leaves the rest of the product working.
2. **Recording or upload.** The samples the provider requires, and nothing more. The user sees
   what they recorded and can discard it before anything is sent.
3. **Creation.** The samples go to the provider; the deployment stores the identifier it gets
   back, and never the audio (D-013). A creation that fails says why in terms of what the user
   can do about it.
4. **Use.** The cloned voice is the first step of the resolution chain above, which is what makes
   a revoked or broken clone degrade instead of failing.
5. **Retrain.** A new set of samples replaces the old voice. The old identifier is deleted at the
   provider before the new one is stored, so a half-finished retrain leaves one voice and not
   two.
6. **Delete.** Removes it at the provider and here, and takes effect on the next call rather than
   eventually. Deleting the account deletes the voice with it.

Every one of these is absent from the interface while `cloning` is false — absent, not disabled.
