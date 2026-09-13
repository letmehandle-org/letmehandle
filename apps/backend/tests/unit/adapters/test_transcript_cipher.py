"""Transcript encryption: confidential, bound to its record, and rotatable without loss."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.security.transcript_cipher import (
    NONCE_BYTES,
    AesGcmTranscriptCipher,
)
from letmehandle.config.settings import ConfigurationError, parse_transcript_keys
from letmehandle.domain.errors import DecryptionError, InvariantError, UnknownKeyError
from letmehandle.domain.ports.security import SealedBytes
from tests.support.config import make_settings

if TYPE_CHECKING:
    from pathlib import Path

KEY_A = ("key-a", bytes(range(32)))
KEY_B = ("key-b", bytes(range(32, 64)))
SAID = b"my parcel reference is 4471"
CONTEXT = ("transcript", "user-1", "call-1", "caller", "2026-06-01T12:00:00.000000+00:00")


def encoded(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode()


# Lists that stop startup while holding at least one real-length key. The first carries a valid
# key ahead of the broken entry, which is what an error echoing its input leaks the most of.
MALFORMED_KEY_LISTS = [
    f"key-a:{encoded(KEY_A[1])},key-b:{encoded(b'sixteen bytes!!!')}",
    f"{encoded(KEY_B[1])}",
    f"Key-A:{encoded(KEY_A[1])}",
    f"key-a:{encoded(KEY_A[1])},key-a:{encoded(KEY_B[1])}",
]


def assert_no_fragment_of(text: str, output: str) -> None:
    """No eight characters in a row of any key in `text` appear anywhere in `output`."""
    secrets = [entry.partition(":")[2] or entry for entry in text.split(",")]
    for secret in (each.strip() for each in secrets):
        for start in range(len(secret) - 7):
            assert secret[start : start + 8] not in output


class TestConfidentiality:
    def test_ciphertext_is_not_the_plaintext(self) -> None:
        sealed = AesGcmTranscriptCipher([KEY_A]).seal(SAID, CONTEXT)
        assert SAID not in sealed.ciphertext
        assert sealed.key_id == "key-a"

    def test_two_seals_of_the_same_text_differ(self) -> None:
        cipher = AesGcmTranscriptCipher([KEY_A])
        first, second = cipher.seal(SAID, CONTEXT), cipher.seal(SAID, CONTEXT)
        assert first.ciphertext != second.ciphertext
        assert first.ciphertext[:NONCE_BYTES] != second.ciphertext[:NONCE_BYTES]

    def test_what_was_sealed_opens(self) -> None:
        cipher = AesGcmTranscriptCipher([KEY_A])
        assert cipher.open(cipher.seal(SAID, CONTEXT), CONTEXT) == SAID

    def test_the_repr_names_key_ids_and_no_key(self) -> None:
        rendered = repr(AesGcmTranscriptCipher([KEY_A, KEY_B]))
        assert "key-a" in rendered
        assert KEY_A[1].hex() not in rendered
        assert str(KEY_A[1]) not in rendered


class TestIntegrity:
    def test_a_flipped_byte_is_refused_with_a_typed_error(self) -> None:
        cipher = AesGcmTranscriptCipher([KEY_A])
        sealed = cipher.seal(SAID, CONTEXT)
        tampered = bytearray(sealed.ciphertext)
        tampered[-1] ^= 1
        with pytest.raises(DecryptionError) as raised:
            cipher.open(SealedBytes(sealed.key_id, bytes(tampered)), CONTEXT)
        assert not isinstance(raised.value, UnknownKeyError)
        assert raised.value.key_id == "key-a"

    @pytest.mark.parametrize(
        "other",
        [
            ("transcript", "user-2", "call-1", "caller", CONTEXT[4]),
            ("transcript", "user-1", "call-2", "caller", CONTEXT[4]),
            ("transcript", "user-1", "call-1", "agent", CONTEXT[4]),
            ("summary", "user-1", "call-1", "caller", CONTEXT[4]),
            # The same characters split differently must not authenticate as the same record.
            ("transcript", "user-1c", "all-1", "caller", CONTEXT[4]),
        ],
    )
    def test_a_different_context_is_refused(self, other: tuple[str, ...]) -> None:
        cipher = AesGcmTranscriptCipher([KEY_A])
        with pytest.raises(DecryptionError):
            cipher.open(cipher.seal(SAID, CONTEXT), other)

    def test_truncated_ciphertext_is_refused(self) -> None:
        with pytest.raises(DecryptionError):
            AesGcmTranscriptCipher([KEY_A]).open(SealedBytes("key-a", b"short"), CONTEXT)

    def test_an_error_never_carries_the_content(self) -> None:
        cipher = AesGcmTranscriptCipher([KEY_A])
        sealed = cipher.seal(SAID, CONTEXT)
        with pytest.raises(DecryptionError) as raised:
            cipher.open(sealed, ("transcript",))
        assert "4471" not in str(raised.value)
        assert "parcel" not in str(raised.value)

    def test_a_key_id_naming_a_different_key_is_refused(self) -> None:
        sealed = AesGcmTranscriptCipher([KEY_A]).seal(SAID, CONTEXT)
        impostor = AesGcmTranscriptCipher([("key-a", KEY_B[1])])
        with pytest.raises(DecryptionError):
            impostor.open(sealed, CONTEXT)


class TestRotation:
    def test_old_and_new_both_read_after_a_key_is_introduced(self) -> None:
        before = AesGcmTranscriptCipher([KEY_A])
        old = before.seal(SAID, CONTEXT)

        after = AesGcmTranscriptCipher([KEY_B, KEY_A])
        new = after.seal(SAID, CONTEXT)

        assert new.key_id == "key-b"
        assert after.open(old, CONTEXT) == SAID
        assert after.open(new, CONTEXT) == SAID

    def test_removing_a_key_still_in_use_fails_by_name(self) -> None:
        old = AesGcmTranscriptCipher([KEY_A]).seal(SAID, CONTEXT)
        with pytest.raises(UnknownKeyError) as raised:
            AesGcmTranscriptCipher([KEY_B]).open(old, CONTEXT)
        assert raised.value.key_id == "key-a"
        assert "put that key back" in str(raised.value)


class TestConstruction:
    def test_no_keys_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            AesGcmTranscriptCipher([])

    def test_a_repeated_id_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            AesGcmTranscriptCipher([KEY_A, ("key-a", KEY_B[1])])

    def test_a_short_key_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            AesGcmTranscriptCipher([("key-a", b"too short")])


class TestConfiguredKeys:
    def test_keys_are_read_newest_first(self) -> None:
        text = f"key-b:{encoded(KEY_B[1])}, key-a:{encoded(KEY_A[1])}"
        assert parse_transcript_keys(text) == (KEY_B, KEY_A)

    def test_standard_base64_is_accepted_too(self) -> None:
        standard = base64.b64encode(bytes([251] * 32)).decode()
        assert parse_transcript_keys(f"k1:{standard}") == (("k1", bytes([251] * 32)),)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            " , ",
            encoded(KEY_A[1]),  # a key pasted without its id
            f"Key-A:{encoded(KEY_A[1])}",
            f"key-a:{base64.urlsafe_b64encode(b'sixteen bytes!!!').decode()}",
            "key-a:not base64 at all!",
            "key-a:QUFB",
        ],
    )
    def test_a_malformed_list_is_refused_without_repeating_it(self, text: str) -> None:
        with pytest.raises(ValueError, match="TRANSCRIPT_ENCRYPTION_KEYS") as raised:
            parse_transcript_keys(text)
        secret = text.partition(":")[2] or text
        if secret.strip(" ,"):
            assert secret not in str(raised.value)

    def test_a_repeated_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="more than once"):
            parse_transcript_keys(f"k:{encoded(KEY_A[1])},k:{encoded(KEY_B[1])}")

    def test_the_keys_are_optional_until_something_needs_them(self) -> None:
        settings = make_settings()
        with pytest.raises(ConfigurationError, match="TRANSCRIPT_ENCRYPTION_KEYS"):
            settings.require_transcript_keys()

    def test_configured_keys_are_returned_and_never_rendered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = f"key-a:{encoded(KEY_A[1])}"
        monkeypatch.setenv("TRANSCRIPT_ENCRYPTION_KEYS", text)
        settings = type(make_settings())()
        assert settings.require_transcript_keys() == (KEY_A,)
        assert encoded(KEY_A[1]) not in repr(settings)

    @pytest.mark.parametrize(
        "text",
        MALFORMED_KEY_LISTS,
        ids=["valid-then-short", "without-an-id", "bad-id", "repeated-id"],
    )
    def test_a_malformed_key_stops_startup_without_printing_any_part_of_any_key(
        self, monkeypatch: pytest.MonkeyPatch, text: str
    ) -> None:
        from letmehandle.config.settings import get_settings

        monkeypatch.setenv("TRANSCRIPT_ENCRYPTION_KEYS", text)
        with pytest.raises(ConfigurationError, match="TRANSCRIPT_ENCRYPTION_KEYS") as raised:
            get_settings()

        printed = [str(raised.value), repr(raised.value)]
        cause = raised.value.__cause__
        while cause is not None:
            printed += [str(cause), repr(cause)]
            cause = cause.__cause__
        assert_no_fragment_of(text, "\n".join(printed))

    def test_a_process_that_fails_to_start_prints_no_part_of_any_key(self, tmp_path: Path) -> None:
        # The whole traceback, chained causes included, as a scheduler's log would keep it.
        text = MALFORMED_KEY_LISTS[0]
        environment = {
            name: value
            for name, value in os.environ.items()
            if name not in {"TRANSCRIPT_ENCRYPTION_KEYS", "APP_ENV"}
        }
        environment["TRANSCRIPT_ENCRYPTION_KEYS"] = text
        finished = subprocess.run(
            [
                sys.executable,
                "-c",
                "from letmehandle.config.settings import get_settings\nget_settings()",
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

        assert finished.returncode != 0
        assert "TRANSCRIPT_ENCRYPTION_KEYS" in finished.stderr
        assert_no_fragment_of(text, finished.stdout + finished.stderr)

    @pytest.mark.parametrize("stray", [" ", "*", "\n", "="])
    def test_a_key_with_anything_but_base64_in_it_is_refused(self, stray: str) -> None:
        # Lenient decoding drops what it does not recognise, so a key damaged in pasting could
        # still decode to thirty-two bytes, just not the thirty-two that sealed anything.
        valid = encoded(KEY_A[1])
        damaged = valid[:10] + stray + valid[10:]
        with pytest.raises(ValueError, match="TRANSCRIPT_ENCRYPTION_KEYS"):
            parse_transcript_keys(f"key-a:{damaged}")

    def test_a_blank_value_counts_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRANSCRIPT_ENCRYPTION_KEYS", "")
        assert type(make_settings())().transcript_encryption_keys is None
