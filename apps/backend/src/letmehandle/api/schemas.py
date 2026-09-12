"""What the API accepts and returns.

Separate from the domain types on purpose. The wire format is a contract with a mobile app that
ships independently and cannot be updated in step; the domain is free to change. Tying them
together means either the domain cannot move or the app breaks.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber


class Request(BaseModel):
    """Base for anything accepted from outside."""

    # Unknown fields are rejected rather than ignored. A client sending `phone_numer` should be
    # told, not silently given a default.
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


class VerifyRequest(Request):
    challenge_id: Annotated[str, Field(min_length=1, max_length=64)]
    code: Annotated[str, Field(min_length=4, max_length=10)]


class RefreshRequest(Request):
    refresh_token: Annotated[str, Field(min_length=1, max_length=512)]


class SignOutRequest(Request):
    refresh_token: Annotated[str, Field(min_length=1, max_length=512)]


class TokenResponse(Response):
    access_token: str
    refresh_token: str
    # S105 reads the name as a password. It is the scheme name from RFC 6750.
    token_type: str = "Bearer"  # noqa: S105
    expires_in_seconds: int


class ProfileResponse(Response):
    id: str
    phone_number: str
    display_name: str | None
    locale: str


class UpdateProfileRequest(Request):
    display_name: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    locale: Annotated[str, Field(min_length=2, max_length=16)] | None = None
