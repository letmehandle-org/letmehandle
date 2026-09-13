"""Every error the product defines has one meaning, decided once.

The test over the whole package is what keeps it that way: an error added without saying what
kind of failure it is fails here, rather than being retried, shown or ignored by whichever guess
the first code to catch it makes.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Final

import pytest

import letmehandle
from letmehandle.application.speech.conversation import ConversationFailedError
from letmehandle.domain.errors import DomainError, ProviderError
from letmehandle.domain.failures import Failure, FailureKind, classify, failure_of

# Errors whose kind depends on the instance: each is decided from whether trying again may work.
DECIDED_PER_INSTANCE: Final[frozenset[type[DomainError]]] = frozenset(
    {ProviderError, ConversationFailedError}
)

# The product's exceptions that are not domain errors, and why none needs a kind of its own. Each
# is caught at the edge it belongs to and translated there, so one reaching `classify` is a defect,
# which is what it is then classified as. Adding one here is the decision to make that claim.
NEVER_LEAVES_ITS_EDGE: Final = frozenset(
    {
        # Adapters' own parse and protocol failures, turned into outcomes where they are raised.
        "UnsupportedConversionError",
        "BodyTooLargeError",
        "AccessTokenError",
        "CredentialError",
        "MalformedEventError",
        "EventConnectionError",
        "ConnectionClosedError",
        "ConnectionFailedError",
        "CallbackMalformedError",
        "MediaProtocolError",
        "_RefusedError",
        "SignatureRejectedError",
        "MediaSocketClosedError",
        # The HTTP layer's own response, and the request refusals that become one.
        "ApiError",
        "ReportingRateLimitedError",
        # A model's arguments that do not parse, which the agent reports as a refused tool call.
        "MalformedArgumentsError",
        # A summary a model could not write, replaced by the one built from the call's facts.
        "SummaryNotWrittenError",
        # Startup refusing to begin, which ends the process rather than reaching anything.
        "ConfigurationError",
        # A metric label or span attribute that could carry content: a defect at its call site.
        "MetricLabelError",
        "SpanAttributeError",
    }
)


def _product_exceptions() -> list[type[BaseException]]:
    for module in pkgutil.walk_packages(letmehandle.__path__, f"{letmehandle.__name__}."):
        importlib.import_module(module.name)
    found: dict[str, type[BaseException]] = {}
    pending: list[type[BaseException]] = [Exception]
    while pending:
        for subclass in pending.pop().__subclasses__():
            pending.append(subclass)
            if subclass.__module__.startswith(f"{letmehandle.__name__}."):
                found[f"{subclass.__module__}.{subclass.__qualname__}"] = subclass
    return sorted(found.values(), key=lambda each: each.__qualname__)


PRODUCT_EXCEPTIONS: Final = _product_exceptions()


@pytest.mark.parametrize("error", PRODUCT_EXCEPTIONS, ids=lambda each: each.__qualname__)
def test_every_error_the_product_defines_says_what_kind_of_failure_it_is(
    error: type[BaseException],
) -> None:
    if issubclass(error, DomainError):
        assert "failure_kind" in vars(error) or error in DECIDED_PER_INSTANCE, (
            f"{error.__qualname__} does not say which FailureKind it is"
        )
    else:
        assert error.__qualname__ in NEVER_LEAVES_ITS_EDGE, (
            f"{error.__qualname__} is neither a domain error with a kind nor listed as translated "
            f"at its edge"
        )


def test_the_list_of_translated_errors_names_only_errors_that_exist() -> None:
    names = {each.__qualname__ for each in PRODUCT_EXCEPTIONS}

    assert names >= NEVER_LEAVES_ITS_EDGE


@pytest.mark.parametrize("error_type", [ProviderError, ConversationFailedError])
def test_an_error_decided_per_instance_is_retryable_exactly_when_it_says_so(
    error_type: type[ProviderError | ConversationFailedError],
) -> None:
    def build(retryable: bool) -> BaseException:
        if error_type is ProviderError:
            return ProviderError("speech", "it said no", retryable=retryable)
        return ConversationFailedError("it said no", retryable=retryable)

    assert classify(build(retryable=True)).kind is FailureKind.UNAVAILABLE
    assert classify(build(retryable=True)).retryable
    assert classify(build(retryable=False)).kind is FailureKind.REFUSED
    assert not classify(build(retryable=False)).retryable


def test_every_kind_has_a_meaning() -> None:
    assert {failure_of(kind).kind for kind in FailureKind} == set(FailureKind)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), FailureKind.TIMEOUT),
        (ConnectionResetError(), FailureKind.UNAVAILABLE),
        (RuntimeError("a mistake"), FailureKind.DEFECT),
        (KeyError("a mistake"), FailureKind.DEFECT),
        (DomainError("one that forgot to say"), FailureKind.DEFECT),
    ],
)
def test_what_nothing_declares_is_decided_from_what_the_standard_library_says_it_is(
    error: BaseException, expected: FailureKind
) -> None:
    assert classify(error).kind is expected


def test_only_a_failure_of_the_world_outside_is_worth_trying_again() -> None:
    retryable = {kind for kind in FailureKind if failure_of(kind).retryable}

    assert retryable == {FailureKind.TIMEOUT, FailureKind.UNAVAILABLE}


def test_a_defect_or_a_sealed_record_is_what_somebody_running_the_deployment_must_act_on() -> None:
    attention = {kind for kind in FailureKind if failure_of(kind).needs_attention}

    assert attention == {FailureKind.DEFECT, FailureKind.SEALED}
    assert failure_of(FailureKind.DEFECT).is_defect
    assert not failure_of(FailureKind.SEALED).is_defect


def test_what_was_wrong_with_a_request_is_what_its_sender_is_told() -> None:
    visible = {kind for kind in FailureKind if failure_of(kind).user_visible}

    assert visible == {
        FailureKind.INVALID,
        FailureKind.NOT_PERMITTED,
        FailureKind.RATE_LIMITED,
        FailureKind.NOT_FOUND,
        FailureKind.CONFLICT,
    }


def test_classifying_never_reads_the_message() -> None:
    class TalkativeError(DomainError):
        failure_kind = FailureKind.INVALID

        def __str__(self) -> str:
            raise AssertionError("the message was read")

    assert classify(TalkativeError()) == Failure(
        FailureKind.INVALID, retryable=False, user_visible=True, needs_attention=False
    )
