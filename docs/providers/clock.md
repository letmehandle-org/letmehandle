# Clock and IdGenerator

Time and identity, injected. Nothing in the domain calls `datetime.now()` or generates an identifier
inline: both are inputs to decisions — the user's hours, how long a phone rings, when a token
expires, a call's identity — and code that reaches for them directly cannot be tested without
waiting or patching a module.

Interface: `apps/backend/src/letmehandle/domain/ports/clock.py`
Contract suites: `ClockContract` and `IdGeneratorContract` in `apps/backend/tests/contracts/other_ports.py`
Implementations: `SystemClock` and `UUIDGenerator` in `adapters/clock.py`; `FixedClock` and
`CountingIdGenerator` in `tests/contracts/fakes.py`

## The interface

| Port | Member | Means |
| --- | --- | --- |
| `Clock` | `now()` | The current instant, always timezone-aware |
| `IdGenerator` | `generate()` | A new, unique string |

Neither declares capabilities: there is nothing optional about telling the time.

A naive instant is refused by the contract. It means whatever the machine is set to, which is how a
service that behaves in one region misbehaves in another.

## Configuration

None. Bootstrap builds one `SystemClock` and one `UUIDGenerator` for the life of the process, and
hands the same clock to everything that needs one, so two parts of a call cannot disagree about when
it is.

## Adding one

A worked example: a clock that reads a trusted time source rather than the host's clock.

```python
class TrustedClock(Clock):
    """The time as a trusted source last reported it, advanced by the monotonic clock."""

    def __init__(self, anchor: datetime, anchored_at: float) -> None:
        if anchor.tzinfo is None:
            raise InvariantError("a clock's anchor carries its timezone")
        self._anchor = anchor
        self._anchored_at = anchored_at

    def now(self) -> datetime:
        return self._anchor + timedelta(seconds=time.monotonic() - self._anchored_at)
```

Prove it with the contract:

```python
class TestTrustedClock(ClockContract):
    @pytest.fixture
    def clock(self) -> TrustedClock:
        return TrustedClock(datetime(2026, 1, 1, tzinfo=UTC), time.monotonic())
```

Then construct it in `build_container` in `bootstrap.py` in place of `SystemClock`. Tests use
`FixedClock`, which moves only when told to, so a test of a thirty-second ring takes no time.

## Testing

```bash
cd apps/backend
uv run pytest tests/contracts/test_other_ports_fakes.py
```
