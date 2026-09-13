"""The domain's own failures.

Every error the domain raises descends from `DomainError`. That is what lets a caller catch
the product's failures without catching a vendor's, and it is why no adapter exception is
allowed to escape into application code: an adapter translates, at its own edge.

Each error carries the facts a caller needs to act, as attributes rather than as prose in a
message. A message is for a human reading a log; an attribute is for the code deciding what
to do next, and for the test asserting it did.
"""

from __future__ import annotations


class DomainError(Exception):
    """Anything the product itself considers a failure."""


class InvariantError(DomainError):
    """A value object was constructed in a state it is not allowed to be in.

    Raised at construction, never later. A type that can exist in an invalid state pushes the
    check to every place that reads it, and one of those places will forget.
    """


class StepNotAskedError(DomainError):
    """A setup step was recorded on a deployment that does not ask it.

    Not an `InvariantError`: the step is real and the request well formed, but answering a
    question this deployment never asks would record something that changes nothing here.
    """

    def __init__(self, step: object) -> None:
        super().__init__(f"{step} is not a step setup asks here")
        self.step = step


class IllegalTransitionError(DomainError):
    """A call was asked to move to a state it cannot reach from where it is."""

    def __init__(self, current: object, requested: object) -> None:
        super().__init__(f"a call in {current} cannot move to {requested}")
        self.current = current
        self.requested = requested


class CapabilityNotSupportedError(DomainError):
    """A provider was asked for something it has declared it cannot do.

    The name of the capability is an attribute because callers branch on it — offering a
    different path, or degrading — and parsing that out of a message is how a string becomes
    an interface nobody meant to publish.
    """

    def __init__(self, provider: str, capability: str) -> None:
        super().__init__(f"{provider} does not support {capability}")
        self.provider = provider
        self.capability = capability


class NotAuthorisedError(DomainError):
    """The assistant was asked to do something the user has not permitted.

    Distinct from `CapabilityNotSupportedError`: that one means it cannot, this one means it may
    not. Collapsing them would let a permission failure look like a technical one, and the
    right response to each is different.
    """

    def __init__(self, action: str) -> None:
        super().__init__(f"the assistant is not authorised to {action}")
        self.action = action


class ProviderError(DomainError):
    """A provider failed in a way the domain must handle rather than propagate.

    Adapters raise this instead of letting a vendor exception escape. `retryable` is the fact
    the caller actually needs; without it, every caller invents its own guess about which
    failures are worth trying again, and they disagree.
    """

    def __init__(self, provider: str, reason: str, *, retryable: bool) -> None:
        super().__init__(f"{provider} failed: {reason}")
        self.provider = provider
        self.reason = reason
        self.retryable = retryable


class UnreachableNumberError(ProviderError):
    """A provider will not deliver to this number, and asking again will not change that.

    Its own type because what somebody can do about it differs from every other provider failure:
    a number mistyped, or one that cannot receive a text, is fixed by the person entering it, while
    an outage or a refused account is fixed by nobody on the other end of the request.
    """

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(provider, reason, retryable=False)


class RecordNotFoundError(DomainError):
    """A stored record this user asked to write against does not exist for them.

    One error for "absent" and for "somebody else's", deliberately. Telling the two apart would
    tell a caller which identifiers belong to other people.
    """

    def __init__(self, kind: str, identifier: str) -> None:
        super().__init__(f"no {kind} {identifier!r} belongs to this user")
        self.kind = kind
        self.identifier = identifier


class AlreadyRecordedError(DomainError):
    """Something that is written once was written again.

    A call's summary is the durable record of what happened. A second one quietly replacing the
    first would make the history say whatever the last writer believed.
    """

    def __init__(self, kind: str, identifier: str) -> None:
        super().__init__(f"the {kind} for {identifier!r} has already been recorded")
        self.kind = kind
        self.identifier = identifier


class DecryptionError(DomainError):
    """Stored ciphertext could not be opened, and nothing was returned in its place.

    Raised for tampered bytes, for ciphertext moved onto another record, and for a key that is
    not the one it was sealed with. Never garbage: authenticated encryption either proves the
    bytes are the ones sealed for this record or refuses them. The message names the key and
    never the content.
    """

    def __init__(self, key_id: str, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                f"ciphertext sealed under key {key_id!r} failed authentication: it was altered, "
                f"moved onto another record, or that key id now names a different key"
            )
        )
        self.key_id = key_id


class UnknownKeyError(DecryptionError):
    """Stored ciphertext names a key this deployment no longer has.

    An operator removed a key while rows sealed under it still exist. The remedy is to put the
    key back — anywhere after the newest — and keep it until no stored row names it.
    """

    def __init__(self, key_id: str) -> None:
        super().__init__(
            key_id,
            f"ciphertext was sealed under key {key_id!r}, which is not configured; put that key "
            f"back in the key list, after the newest, until no stored row names it",
        )
