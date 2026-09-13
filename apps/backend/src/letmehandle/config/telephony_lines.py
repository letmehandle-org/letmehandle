"""How the telephony lines a deployment carries calls on are written in its environment.

A line is one account with a streaming provider, the numbers on it, and the regions whose users
forward their calls to it. A deployment serving two countries has a line for each, so that every
user forwards to a local number and is rung from one when they are brought into a call.

Written as compact text, for the reason the voice catalogue gives: JSON inside an environment
variable is where configuration gets silently truncated. The auth tokens are a variable of their
own, so the one holding secrets is never the one somebody pastes into a ticket to ask why a line
does not start.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

from letmehandle.config.listing import entries, repeated
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.region import TelephonyRegion, region_named


class LineProviderName(StrEnum):
    """Which streaming provider a line is an account with."""

    TWILIO = "twilio"


# How TELEPHONY_LINES is written, quoted in every error about it so the fix is in the message.
TELEPHONY_LINES_FORMAT: Final = (
    "name:provider=twilio;regions=US|IN;numbers=+E164|+E164;account=id;app=id;"
    "webhook=https://host,name:..."
)
# How TELEPHONY_LINE_AUTH_TOKENS is written.
LINE_TOKENS_FORMAT: Final = "name:token,name:token"
# What `regions=` says for a line that serves whoever no other line does.
EVERY_REGION: Final = "*"

# A line's name becomes a path segment, so it is kept to what needs no escaping anywhere.
_LINE_NAME: Final = re.compile(r"[a-z][a-z0-9-]{0,15}")
_KEYS: Final = frozenset({"provider", "regions", "numbers", "account", "app", "webhook"})


@dataclass(frozen=True, slots=True)
class TelephonyLine:
    """One line, present and checked: everything a streaming transport for it needs.

    `name` is None for the one line `TELEPHONY_PROVIDER` configures, whose routes have always been
    at the root. `regions` is None for a line that serves every region no other line serves.
    `webhook_base_url` has no trailing slash, so a path can be appended to it without producing a
    URL that differs by one character from the one the provider signed.
    """

    name: str | None
    provider: LineProviderName
    regions: frozenset[TelephonyRegion] | None
    account_id: str
    auth_token: str = field(repr=False)
    numbers: tuple[PhoneNumber, ...]
    app_id: str
    webhook_base_url: str


@dataclass(frozen=True, slots=True)
class LineDescription:
    """One line as TELEPHONY_LINES describes it, which is everything but its token."""

    name: str
    provider: LineProviderName
    regions: frozenset[TelephonyRegion] | None
    numbers: tuple[PhoneNumber, ...]
    account_id: str
    app_id: str
    webhook_base_url: str

    def with_token(self, auth_token: str) -> TelephonyLine:
        """The line, complete."""
        return TelephonyLine(
            name=self.name,
            provider=self.provider,
            regions=self.regions,
            account_id=self.account_id,
            auth_token=auth_token,
            numbers=self.numbers,
            app_id=self.app_id,
            webhook_base_url=self.webhook_base_url,
        )


def parse_telephony_lines(text: str) -> tuple[LineDescription, ...]:
    """The lines TELEPHONY_LINES lists, in its order.

    Every error names the line and the key, never a value: a number or an account id pasted into
    an issue is still a number or an account id.
    """
    listed = entries(text)
    if not listed:
        raise ValueError(f"TELEPHONY_LINES lists no lines; expected {TELEPHONY_LINES_FORMAT!r}")
    lines = tuple(_line(position, entry) for position, entry in enumerate(listed, 1))
    if repeated(line.name for line in lines):
        raise ValueError("TELEPHONY_LINES names the same line more than once")
    _refuse_shared_regions(lines)
    return lines


def parse_line_tokens(text: str) -> dict[str, str]:
    """Each line's auth token, by the line's name. Errors name a position, never a token."""
    tokens: dict[str, str] = {}
    for position, entry in enumerate(entries(text), 1):
        name, separator, token = (part.strip() for part in entry.partition(":"))
        if not separator or not _LINE_NAME.fullmatch(name) or not token:
            raise ValueError(
                f"TELEPHONY_LINE_AUTH_TOKENS entry {position} is not in the form "
                f"{LINE_TOKENS_FORMAT!r}"
            )
        if name in tokens:
            raise ValueError(f"TELEPHONY_LINE_AUTH_TOKENS gives line {name!r} more than one token")
        tokens[name] = token
    if not tokens:
        raise ValueError(
            f"TELEPHONY_LINE_AUTH_TOKENS lists no tokens; expected {LINE_TOKENS_FORMAT!r}"
        )
    return tokens


def _line(position: int, entry: str) -> LineDescription:
    name, separator, body = (part.strip() for part in entry.partition(":"))
    if not separator or not _LINE_NAME.fullmatch(name):
        raise ValueError(
            f"TELEPHONY_LINES entry {position} does not start with a name, 1-16 lower-case "
            f"letters, digits or -, and a colon; expected {TELEPHONY_LINES_FORMAT!r}"
        )
    pairs: dict[str, str] = {}
    for item in entries(body, ";"):
        key, equals, value = (part.strip() for part in item.partition("="))
        if not equals or key not in _KEYS or not value:
            raise ValueError(
                f"TELEPHONY_LINES line {name!r} has an entry that is not one of "
                f"{', '.join(sorted(_KEYS))} with a value"
            )
        if key in pairs:
            raise ValueError(f"TELEPHONY_LINES line {name!r} gives {key} more than once")
        pairs[key] = value
    missing = sorted(_KEYS - set(pairs))
    if missing:
        raise ValueError(f"TELEPHONY_LINES line {name!r} does not give {', '.join(missing)}")
    return LineDescription(
        name=name,
        provider=_provider(name, pairs["provider"]),
        regions=_regions(name, pairs["regions"]),
        numbers=_numbers(name, pairs["numbers"]),
        account_id=pairs["account"],
        app_id=pairs["app"],
        webhook_base_url=_webhook(name, pairs["webhook"]),
    )


def _provider(name: str, value: str) -> LineProviderName:
    try:
        return LineProviderName(value)
    except ValueError:
        raise ValueError(
            f"TELEPHONY_LINES line {name!r} names a provider that is not one of "
            f"{', '.join(provider.value for provider in LineProviderName)}"
        ) from None


def _regions(name: str, value: str) -> frozenset[TelephonyRegion] | None:
    if value == EVERY_REGION:
        return None
    try:
        return frozenset(region_named(each) for each in value.split("|"))
    except InvariantError as error:
        raise ValueError(f"TELEPHONY_LINES line {name!r}: {error}") from None


def _numbers(name: str, value: str) -> tuple[PhoneNumber, ...]:
    numbers: list[PhoneNumber] = []
    for position, entry in enumerate(value.split("|"), 1):
        try:
            numbers.append(PhoneNumber.parse(entry))
        except InvariantError:
            raise ValueError(
                f"TELEPHONY_LINES line {name!r} number {position} is not an international number "
                "in E.164 form"
            ) from None
    return tuple(numbers)


def _webhook(name: str, value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"TELEPHONY_LINES line {name!r} has a webhook that is not an http URL")
    if parts.query or parts.fragment:
        raise ValueError(
            f"TELEPHONY_LINES line {name!r} has a webhook with a query or a fragment, which a "
            "path appended to it would not survive"
        )
    return value.rstrip("/")


def _refuse_shared_regions(lines: tuple[LineDescription, ...]) -> None:
    """Refuse two lines for one region, which would leave its users two numbers to forward to."""
    if sum(line.regions is None for line in lines) > 1:
        raise ValueError(f"TELEPHONY_LINES has more than one line serving {EVERY_REGION!r}")
    served: set[TelephonyRegion] = set()
    for line in lines:
        for region in sorted(line.regions or (), key=_by_name):
            if region in served:
                raise ValueError(
                    f"TELEPHONY_LINES has more than one line serving region {region.name}"
                )
            served.add(region)


def _by_name(region: TelephonyRegion) -> str:
    return region.name
