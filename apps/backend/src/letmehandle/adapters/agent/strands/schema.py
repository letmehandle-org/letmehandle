"""Pydantic building blocks for the answers a model writes through the SDK's structured output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator, StrictStr, WithJsonSchema

if TYPE_CHECKING:
    from collections.abc import Iterable


def one_of(options: Iterable[str]) -> WithJsonSchema:
    """A schema listing exactly `options`, without the enumeration's docstring."""
    return WithJsonSchema({"type": "string", "enum": list(options)})


def says_something(value: str) -> str:
    """`value`, refused when blank, where the model is told why."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


type Text = Annotated[StrictStr, AfterValidator(says_something)]
