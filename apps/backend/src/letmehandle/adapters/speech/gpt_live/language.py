"""Language rules written in the language they ask for, and the language a caller spoke (D-040)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from letmehandle.adapters.speech.session_support.offer import base_language

if TYPE_CHECKING:
    from collections.abc import Sequence

# Letters an utterance needs, in one script, before it says which language the caller is speaking.
MINIMUM_LETTERS: Final = 8
# The share of an utterance's letters that must be in that script.
MINIMUM_SHARE: Final = 0.6


@dataclass(frozen=True, slots=True)
class _Phrasing:
    """What the model is told about one language, written in that language."""

    rule: str
    switch: str
    greet: str
    resume: str


_PHRASINGS: Final = {
    "en": _Phrasing(
        rule="Speak English in this conversation unless the caller speaks another language.",
        switch="The caller is now speaking English. Reply in English from now on, until the "
        "caller changes language.",
        greet='Greet the caller now, without waiting for them to speak, with: "{greeting}" '
        "Then pause and listen.",
        resume="The connection to this conversation was lost and has been restored. Do not greet "
        "the caller again; carry on from where the conversation was.",
    ),
    "hi": _Phrasing(
        rule="इस बातचीत में हिंदी में ही बात करें, जब तक कॉलर कोई और भाषा न बोलने लगे।",
        switch="कॉलर अब हिंदी में बात कर रहे हैं। अब से हिंदी में ही जवाब दें, जब तक कॉलर भाषा न बदलें।",
        greet='कॉलर के बोलने का इंतज़ार किए बिना अभी यह अभिवादन कहें: "{greeting}" फिर रुककर सुनें।',
        resume="इस बातचीत का कनेक्शन टूट गया था और अब फिर से जुड़ गया है। कॉलर का दोबारा अभिवादन न "
        "करें; बातचीत वहीं से आगे बढ़ाएँ।",
    ),
}

# What the model is told to begin with once its instructions for the opening are in.
BEGIN: Final = "Begin the conversation now, following the instructions provided."

# Each script's Unicode blocks and its languages; Latin counts only letters.
_SCRIPTS: Final = (
    ("latin", ((0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F)), ("en", "es", "fr", "de", "pt", "it")),
    ("devanagari", ((0x900, 0x97F), (0xA8E0, 0xA8FF)), ("hi", "mr", "ne")),
    ("bengali", ((0x980, 0x9FF),), ("bn", "as")),
    ("gurmukhi", ((0xA00, 0xA7F),), ("pa",)),
    ("gujarati", ((0xA80, 0xAFF),), ("gu",)),
    ("tamil", ((0xB80, 0xBFF),), ("ta",)),
    ("telugu", ((0xC00, 0xC7F),), ("te",)),
    ("kannada", ((0xC80, 0xCFF),), ("kn",)),
    ("malayalam", ((0xD00, 0xD7F),), ("ml",)),
)


def opening(language: str, greeting: str) -> str:
    """The instruction that opens a session: the language rule, then the greeting to say first."""
    phrasing = _PHRASINGS.get(language)
    if phrasing is None:
        return (
            f"Speak the language with the code {language} in this conversation unless the caller "
            f"speaks another language. Greet the caller now, without waiting for them to speak, "
            f'with: "{greeting}" Then pause and listen.'
        )
    return f"{phrasing.rule} {phrasing.greet.format(greeting=greeting)}"


def resumption(language: str) -> str:
    """The instruction for a session that replaces a dropped one: no greeting, same language."""
    phrasing = _PHRASINGS.get(language)
    if phrasing is None:
        return (
            f"Speak the language with the code {language}. The connection to this conversation "
            "was lost and has been restored. Do not greet the caller again."
        )
    return f"{phrasing.rule} {phrasing.resume}"


def switch(language: str) -> str:
    """The instruction to change to `language` and stay in it."""
    phrasing = _PHRASINGS.get(language)
    if phrasing is None:
        return (
            f"The caller is now speaking the language with the code {language}. Reply in it from "
            "now on, until the caller changes language."
        )
    return phrasing.switch


def spoken_language(text: str, languages: Sequence[str]) -> str | None:
    """The one listed language `text` is clearly written in, or `None` when it is not clear."""
    listed = {base_language(each) for each in languages}
    counts = dict.fromkeys((name for name, _, _ in _SCRIPTS), 0)
    letters = 0
    for character in text:
        script = _script_of(character)
        if script is not None:
            counts[script] += 1
            letters += 1
        elif character.isalpha():
            letters += 1
    script, most = max(counts.items(), key=lambda each: each[1])
    if most < MINIMUM_LETTERS or most < letters * MINIMUM_SHARE:
        return None
    candidates = [each for each in _languages_of(script) if each in listed]
    return candidates[0] if len(candidates) == 1 else None


def _script_of(character: str) -> str | None:
    point = ord(character)
    for name, blocks, _ in _SCRIPTS:
        if any(low <= point <= high for low, high in blocks):
            # Marks count with their script: Devanagari writes its vowel signs as marks.
            return name if name != "latin" or character.isalpha() else None
    return None


def _languages_of(script: str) -> tuple[str, ...]:
    return next(languages for name, _, languages in _SCRIPTS if name == script)
