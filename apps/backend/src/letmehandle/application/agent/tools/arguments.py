"""Reads a model's untrusted tool arguments and describes them, from the same option tables."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable
    from enum import Enum

type Schema = Mapping[str, object]


class MalformedArgumentsError(ValueError):
    """The arguments do not have the shape the tool described."""


# How much text a model may put in one short field.
SHORT_TEXT_CHARACTERS: Final = 280


def options_by_value[E: Enum](kind: type[E]) -> Mapping[str, E]:
    """Every member of a string enum, keyed by the value a model sends."""
    return {str(member.value): member for member in kind}


def options_by_name[E: Enum](kind: type[E]) -> Mapping[str, E]:
    """Every member, keyed by its lower-case name, for an ordered enum whose values are numbers."""
    return {member.name.lower(): member for member in kind}


def expect_only(arguments: Mapping[str, object], accepted: Iterable[str]) -> None:
    """Refuses a field the tool never described."""
    unknown = sorted(set(arguments) - set(accepted))
    if unknown:
        raise MalformedArgumentsError(f"unexpected arguments: {', '.join(unknown)}")


def required_text(arguments: Mapping[str, object], name: str, *, limit: int) -> str:
    """Text that must be present and say something, at most `limit` characters once trimmed."""
    text = optional_text(arguments, name, limit=limit)
    if text is None:
        raise MalformedArgumentsError(f"{name} is required")
    return text


def optional_text(arguments: Mapping[str, object], name: str, *, limit: int) -> str | None:
    """Text that may be absent or null, and says something if it is there."""
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedArgumentsError(f"{name} must be text")
    text = value.strip()
    if not text:
        raise MalformedArgumentsError(f"{name} must not be blank")
    if len(text) > limit:
        raise MalformedArgumentsError(f"{name} is at most {limit} characters")
    return text


def flag(arguments: Mapping[str, object], name: str, *, default: bool = False) -> bool:
    """True or false, `default` when absent. Only a real boolean counts: not "yes", not 1."""
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise MalformedArgumentsError(f"{name} must be true or false")
    return value


def required_choice[E: Enum](
    arguments: Mapping[str, object], name: str, options: Mapping[str, E]
) -> E:
    """One of a fixed set of options, which must be present."""
    choice = optional_choice(arguments, name, options)
    if choice is None:
        raise MalformedArgumentsError(f"{name} is required")
    return choice


def optional_choice[E: Enum](
    arguments: Mapping[str, object], name: str, options: Mapping[str, E]
) -> E | None:
    """One of a fixed set of options, or absent or null. Matched exactly, never guessed at."""
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or value not in options:
        raise MalformedArgumentsError(f"{name} must be one of: {', '.join(options)}")
    return options[value]


def objects(
    arguments: Mapping[str, object], name: str, *, limit: int
) -> tuple[Mapping[str, object], ...]:
    """A list of at most `limit` objects, empty when absent or null."""
    value = arguments.get(name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MalformedArgumentsError(f"{name} must be a list")
    if len(value) > limit:
        raise MalformedArgumentsError(f"{name} holds at most {limit} entries")
    if not all(isinstance(item, Mapping) for item in value):
        raise MalformedArgumentsError(f"every entry in {name} must be an object")
    return tuple(value)


def object_schema(properties: Mapping[str, Schema], *required: str) -> Schema:
    """An object with exactly these fields, and no others."""
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def text_schema(description: str, *, limit: int) -> Schema:
    return {"type": "string", "description": description, "minLength": 1, "maxLength": limit}


def choice_schema(description: str, options: Mapping[str, Enum]) -> Schema:
    return {"type": "string", "description": description, "enum": list(options)}


def flag_schema(description: str) -> Schema:
    return {"type": "boolean", "description": description}


def list_schema(description: str, items: Schema, *, limit: int) -> Schema:
    return {"type": "array", "description": description, "items": items, "maxItems": limit}
