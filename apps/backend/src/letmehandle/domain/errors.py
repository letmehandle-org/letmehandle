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
