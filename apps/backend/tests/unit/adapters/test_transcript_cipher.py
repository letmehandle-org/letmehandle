"""Transcript encryption: confidential, bound to its record, and rotatable without loss."""

from __future__ import annotations

import base64

import pytest

from letmehandle.adapters.security.transcript_cipher import (
    NONCE_BYTES,
    AesGcmTranscriptCipher,
)
from letmehandle.config.settings import ConfigurationError, parse_transcript_keys
from letmehandle.domain.errors import DecryptionError, InvariantError, UnknownKeyError
from letmehandle.domain.ports.security import SealedBytes
from tests.support.config import make_settings

KEY_A = ("key-a", bytes(range(32)))
KEY_B = ("key-b", bytes(range(32, 64)))
SAID = b"my parcel reference is 4471"
CONTEXT = ("transcript", "user-1", "call-1", "caller", "2026-06-01T12:00:00.000000+00:00")


def encoded(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode()


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

    def test_a_malformed_key_stops_startup_without_printing_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from letmehandle.config.settings import get_settings

        short = base64.urlsafe_b64encode(b"sixteen bytes!!!").decode()
        monkeypatch.setenv("TRANSCRIPT_ENCRYPTION_KEYS", f"key-a:{short}")
        with pytest.raises(ConfigurationError, match="TRANSCRIPT_ENCRYPTION_KEYS") as raised:
            get_settings()
        assert short not in str(raised.value)

    def test_a_blank_value_counts_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRANSCRIPT_ENCRYPTION_KEYS", "")
        assert type(make_settings())().transcript_encryption_keys is None
