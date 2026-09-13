"""What the API accepts and returns, kept apart from the domain types."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.notification import DevicePlatform

# A language tag, so an unspeakable locale never reaches the agent or a voice.
LocaleTag = Annotated[
    str, Field(min_length=2, max_length=16, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
]


class Request(BaseModel):
    """Base for anything accepted from outside."""

    # Unknown fields are refused, not ignored.
    model_config = ConfigDict(extra="forbid")


class Response(BaseModel):
    """Base for anything returned."""


class ChallengeRequest(Request):
    phone_number: Annotated[str, Field(min_length=5, max_length=20)]

    @field_validator("phone_number")
    @classmethod
    def _must_be_a_number(cls, value: str) -> str:
        """Normalise at the boundary, so nothing downstream sees an unnormalised number."""
        try:
            return PhoneNumber.parse(value).value
        except InvariantError as error:
            raise ValueError(str(error)) from error


class ChallengeResponse(Response):
    challenge_id: str
    expires_in_seconds: int
    # Seconds until another code may be asked for.
    resend_after_seconds: int


class VerifyRequest(Request):
    challenge_id: Annotated[str, Field(min_length=1, max_length=64)]
    code: Annotated[str, Field(min_length=4, max_length=10)]


class RefreshRequest(Request):
    refresh_token: Annotated[str, Field(min_length=1, max_length=512)]


# The characters a push token is made of on either platform.
PushTokenValue = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9._:-]+$")]


class DevicePayload(Request):
    """One device, as its platform identifies it."""

    platform: DevicePlatform
    token: PushTokenValue


class SignOutRequest(Request):
    refresh_token: Annotated[str, Field(min_length=1, max_length=512)]
    # The device signing out, which stops receiving the account's notifications.
    device: DevicePayload | None = None


class TokenResponse(Response):
    access_token: str
    refresh_token: str
    # The scheme name from RFC 6750.
    token_type: str = "Bearer"  # noqa: S105
    expires_in_seconds: int


class CallForwardingResponse(Response):
    """Where the user's phone should forward the calls it does not take."""

    number: Annotated[
        str,
        Field(
            description=(
                "The number, in E.164 form, to set the phone's conditional call forwarding to: "
                "calls that go unanswered and calls that arrive while the line is busy."
            )
        ),
    ]


class ProfileResponse(Response):
    id: str
    phone_number: str
    display_name: str | None
    locale: str
    call_forwarding: Annotated[
        CallForwardingResponse | None,
        Field(
            description=(
                "Present when calls reach the assistant only by being forwarded: until the user's "
                "phone forwards unanswered and busy calls to this number, no call reaches it. "
                "Null when nothing needs forwarding."
            )
        ),
    ]


class UpdateProfileRequest(Request):
    display_name: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    locale: LocaleTag | None = None
