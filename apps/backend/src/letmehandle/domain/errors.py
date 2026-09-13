"""The domain's own failures, each carrying the facts a caller acts on as attributes."""

from __future__ import annotations

from letmehandle.domain.failures import FailureKind


class DomainError(Exception):
    """Anything the product considers a failure; one that names no kind is a defect."""

    failure_kind: FailureKind = FailureKind.DEFECT


class InvariantError(DomainError):
    """A value was constructed in a state it is not allowed to be in."""

    failure_kind = FailureKind.INVALID


class StepNotAskedError(DomainError):
    """A setup step was recorded on a deployment that does not ask it."""

    failure_kind = FailureKind.INVALID

    def __init__(self, step: object) -> None:
        super().__init__(f"{step} is not a step setup asks here")
        self.step = step


class IllegalTransitionError(DomainError):
    """A call was asked to move to a state it cannot reach from where it is."""

    failure_kind = FailureKind.DEFECT

    def __init__(self, current: object, requested: object) -> None:
        super().__init__(f"a call in {current} cannot move to {requested}")
        self.current = current
        self.requested = requested


class CapabilityNotSupportedError(DomainError):
    """A provider was asked for something it has declared it cannot do."""

    failure_kind = FailureKind.REFUSED

    def __init__(self, provider: str, capability: str) -> None:
        super().__init__(f"{provider} does not support {capability}")
        self.provider = provider
        self.capability = capability


class ProviderError(DomainError):
    """A provider failed, translated at the adapter's edge, saying whether a retry may help."""

    def __init__(self, provider: str, reason: str, *, retryable: bool) -> None:
        super().__init__(f"{provider} failed: {reason}")
        self.provider = provider
        self.reason = reason
        self.retryable = retryable
        self.failure_kind = FailureKind.UNAVAILABLE if retryable else FailureKind.REFUSED


class DeliveryUncertainError(ProviderError):
    """A request reached a provider, which never said whether it acted on it."""

    failure_kind = FailureKind.UNAVAILABLE

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(provider, reason, retryable=True)


class UnreachableNumberError(ProviderError):
    """A provider will not deliver to this number, which only the person entering it can fix."""

    failure_kind = FailureKind.REFUSED

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(provider, reason, retryable=False)


class RecordNotFoundError(DomainError):
    """A record does not exist for this user, whether absent or somebody else's."""

    failure_kind = FailureKind.NOT_FOUND

    def __init__(self, kind: str, identifier: str) -> None:
        super().__init__(f"no {kind} {identifier!r} belongs to this user")
        self.kind = kind
        self.identifier = identifier


class AlreadyRecordedError(DomainError):
    """Something written once was written again."""

    failure_kind = FailureKind.CONFLICT

    def __init__(self, kind: str, identifier: str) -> None:
        super().__init__(f"the {kind} for {identifier!r} has already been recorded")
        self.kind = kind
        self.identifier = identifier


class DecryptionError(DomainError):
    """Stored ciphertext failed authentication; the message names the key, never the content."""

    failure_kind = FailureKind.SEALED

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
    """Stored ciphertext names a key this deployment no longer has."""

    failure_kind = FailureKind.SEALED

    def __init__(self, key_id: str) -> None:
        super().__init__(
            key_id,
            f"ciphertext was sealed under key {key_id!r}, which is not configured; put that key "
            f"back in the key list, after the newest, until no stored row names it",
        )


class StorageUnavailableError(DomainError):
    """The database could not be reached, or dropped the connection a unit of work was using."""

    failure_kind = FailureKind.UNAVAILABLE

    def __init__(self) -> None:
        super().__init__("storage could not be reached")
