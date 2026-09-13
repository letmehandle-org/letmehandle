"""Building the notification: what it says, what it leaves out, and how it gives way to a limit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from letmehandle.application.escalation import notification as module
from letmehandle.application.escalation.notification import (
    ELLIPSIS,
    PHRASEBOOKS,
    EscalationPhrasebook,
    notification_for,
    phrasebook_for,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import (
    MAX_CALL_ID_LENGTH,
    MAX_CALLER_LABEL_LENGTH,
    MAX_DETAIL_LENGTH,
    EscalationContext,
    EscalationStatus,
)
from letmehandle.domain.models.identifiers import CallId

if TYPE_CHECKING:
    from collections.abc import Callable

    from letmehandle.domain.ports.notification import EscalationNotification

RAISED = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)


def context(**overrides: object) -> EscalationContext:
    values: dict[str, object] = {
        "call_id": CallId("call-1"),
        "reason": EscalationReason.DECISION_NEEDS_THE_USER,
        "raised_at": RAISED,
        "caller_label": "a courier",
        "established": "They are at the gate with a parcel.",
        "needed": "Where to leave it.",
    }
    values.update(overrides)
    return EscalationContext(**values)  # type: ignore[arg-type]


def size(notification: EscalationNotification) -> int:
    """A stand-in for a platform's encoding: everything the notification carries, as JSON."""
    return len(
        json.dumps(
            {
                "title": notification.title,
                "body": notification.body,
                "caller": notification.caller_label,
                "data": notification.data,
            },
            ensure_ascii=False,
        ).encode()
    )


def within(limit: int) -> Callable[[EscalationNotification], bool]:
    return lambda notification: size(notification) <= limit


class TestWhatItSays:
    def test_it_carries_why_who_what_is_needed_and_what_is_known(self) -> None:
        built = notification_for(context())
        assert built.title == "There is a decision only you can make"
        assert built.caller_label == "a courier"
        assert (
            built.body
            == "Needs from you: Where to leave it.\nSo far: They are at the gate with a parcel."
        )
        assert built.call_id == CallId("call-1")

    def test_the_data_binds_it_to_the_call_and_nothing_more(self) -> None:
        # Identifiers and categories only: the words are in the visible fields, once.
        assert notification_for(context()).data == {
            "kind": "escalation",
            "call_id": "call-1",
            "reason": "decision_needs_the_user",
            "status": "live",
        }

    def test_an_ended_call_says_so_in_the_data(self) -> None:
        ended = context().ended(RAISED)
        assert notification_for(ended).data["status"] == EscalationStatus.ENDED.value

    def test_an_unknown_caller_is_named_as_unknown(self) -> None:
        assert notification_for(context(caller_label=None)).caller_label == "Unknown caller"

    def test_with_nothing_known_it_still_says_something(self) -> None:
        built = notification_for(context(needed=None, established=None))
        assert built.body == "Nothing more is known yet."

    def test_only_what_is_known_is_listed(self) -> None:
        assert (
            notification_for(context(established=None)).body == "Needs from you: Where to leave it."
        )

    @pytest.mark.parametrize("reason", list(EscalationReason))
    def test_every_reason_has_a_sentence(self, reason: EscalationReason) -> None:
        assert notification_for(context(reason=reason)).title.strip()

    def test_it_is_deterministic(self) -> None:
        assert notification_for(context(), fits=within(150)) == notification_for(
            context(), fits=within(150)
        )


class TestLocales:
    @pytest.mark.parametrize("locale", ["en", "EN_gb", " en-US ", "fr-FR"])
    def test_the_closest_phrasebook_is_used_and_english_is_the_fallback(self, locale: str) -> None:
        assert phrasebook_for(locale) is PHRASEBOOKS["en"]

    @pytest.mark.parametrize("locale", ["hi", "hi-IN"])
    def test_a_hindi_user_is_told_in_hindi(self, locale: str) -> None:
        shown = notification_for(
            context(reason=EscalationReason.CALLER_ASKED_FOR_THE_USER, caller_label=None),
            locale=locale,
        )
        assert shown.title == "कॉल करने वाले ने आपसे बात करनी चाही"
        assert shown.caller_label == "अनजान कॉलर"

    def test_a_full_locale_is_preferred_to_its_language(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        british = EscalationPhrasebook(
            why=PHRASEBOOKS["en"].why,
            unknown_caller="Someone unknown",
            needed_prefix="Needs:",
            established_prefix="Known:",
            nothing_known_yet="Nothing yet.",
        )
        monkeypatch.setattr(module, "PHRASEBOOKS", {**PHRASEBOOKS, "en-gb": british})
        assert phrasebook_for("en_GB") is british
        assert notification_for(context(caller_label=None), locale="en-GB").caller_label == (
            "Someone unknown"
        )

    def test_a_phrasebook_missing_a_reason_stops_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        incomplete = EscalationPhrasebook(
            why={},
            unknown_caller="x",
            needed_prefix="x",
            established_prefix="x",
            nothing_known_yet="x",
        )
        monkeypatch.setattr(module, "PHRASEBOOKS", {"en": incomplete})
        with pytest.raises(InvariantError, match="cannot explain"):
            module._every_reason_has_words()


class TestTrimming:
    def test_a_notification_that_fits_is_untouched(self) -> None:
        assert notification_for(context(), fits=within(4096)) == notification_for(context())

    def test_what_is_established_gives_way_first(self) -> None:
        full = notification_for(context())
        built = notification_for(context(), fits=within(size(full) - 10))
        assert built.body.startswith("Needs from you: Where to leave it.\nSo far: They are")
        assert built.body.endswith(ELLIPSIS)
        assert built.caller_label == "a courier"
        assert size(built) <= size(full) - 10

    def test_the_longest_prefix_that_fits_is_kept(self) -> None:
        full = notification_for(context())
        limit = size(full) - 10
        built = notification_for(context(), fits=within(limit))
        # One more character of what was established would not have fitted.
        kept = built.body.split("So far: ", 1)[1].removesuffix(ELLIPSIS)
        longer = context(established=context().established)
        assert kept == longer.established[: len(kept)].rstrip()  # type: ignore[index]
        assert len(kept) < len("They are at the gate with a parcel.")

    def test_across_every_limit_the_order_of_loss_holds_and_the_result_fits(self) -> None:
        # Every limit from "nothing fits" to "everything fits", so the order of loss is proven
        # rather than sampled: a detail is gone or cut before the caller is touched, the caller
        # is at its shortest before the reason is touched, and anything at or above the floor
        # fits.
        full = size(notification_for(context()))
        floor = size(notification_for(context(), fits=within(1)))
        for limit in range(floor, full + 1):
            built = notification_for(context(), fits=within(limit))
            assert size(built) <= limit, limit
            if built.caller_label != "a courier":
                assert "So far:" not in built.body, limit
            if built.title != "There is a decision only you can make":
                assert built.caller_label == f"a{ELLIPSIS}", limit

    def test_the_caller_and_the_reason_are_cut_when_they_must_be(self) -> None:
        floor = size(notification_for(context(), fits=within(1)))
        built = notification_for(context(), fits=within(floor + 3))
        assert built.caller_label == f"a{ELLIPSIS}"
        assert built.title.endswith(ELLIPSIS)

    def test_when_nothing_fits_the_shortest_is_returned_rather_than_an_error(self) -> None:
        built = notification_for(context(), fits=within(1))
        assert built.title == f"T{ELLIPSIS}"
        assert built.caller_label == f"a{ELLIPSIS}"
        assert built.body == "Nothing more is known yet."

    def test_a_cut_never_ends_in_a_space_before_the_mark(self) -> None:
        trimmed = module._prefix("Where to leave", 6)
        assert trimmed == f"Where{ELLIPSIS}"
        assert module._prefix("   x", 2) == f"  {ELLIPSIS}"
        assert module._prefix("anything", 0) is None

    def test_the_largest_context_allowed_fits_a_platform_limit_after_trimming(self) -> None:
        # The bounds on a stored context exist so that this always holds: whatever the
        # orchestrator records, a 4 KB platform receives a notification rather than a refusal.
        widest = "界" * MAX_DETAIL_LENGTH
        largest = context(
            call_id=CallId("c" * MAX_CALL_ID_LENGTH),
            caller_label="界" * MAX_CALLER_LABEL_LENGTH,
            established=widest,
            needed=widest,
        )
        built = notification_for(largest, fits=within(4096))
        assert size(built) <= 4096
        assert built.body.startswith("Needs from you: 界")

    def test_an_absent_detail_is_skipped_rather_than_cut(self) -> None:
        sparse = context(established=None)
        built = notification_for(sparse, fits=within(size(notification_for(sparse)) - 3))
        assert built.body.startswith("Needs from you: Where to")
        assert built.body.endswith(ELLIPSIS)
