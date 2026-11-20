import time

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.config import SecurityKey
from app.core.security import (
    InvalidApiKeyError,
    InvalidBindingError,
    InvalidCiphertextError,
    PasswordService,
    SecretCipher,
    generate_session_token,
    hash_session_token,
    mint_api_key,
    parse_api_key,
    secrets_match,
)


def key(kid: str) -> SecurityKey:
    return SecurityKey(kid=kid, key=SecretStr(SecretCipher.generate_key()))


def test_round_trip_with_binding() -> None:
    cipher = SecretCipher([key("k1")])
    kid, ciphertext = cipher.encrypt(b"refresh-token", binding="client-a:google:sub-1")
    assert kid == "k1"
    assert b"refresh-token" not in ciphertext
    assert cipher.decrypt(ciphertext, binding="client-a:google:sub-1") == b"refresh-token"


def test_swapped_ciphertext_fails_binding() -> None:
    cipher = SecretCipher([key("k1")])
    _, ciphertext = cipher.encrypt(b"token-a", binding="client-a:google:sub-1")
    with pytest.raises(InvalidBindingError):
        cipher.decrypt(ciphertext, binding="client-b:google:sub-2")


def test_unknown_key_is_reported_as_invalid_ciphertext() -> None:
    _, ciphertext = SecretCipher([key("old")]).encrypt(b"x", binding="b")
    with pytest.raises(InvalidCiphertextError):
        SecretCipher([key("other")]).decrypt(ciphertext, binding="b")


def test_rotation_keeps_timestamp_and_moves_to_primary() -> None:
    old_key, new_key = key("k2025"), key("k2026")
    _, old_ciphertext = SecretCipher([old_key]).encrypt(b"token", binding="b")
    rotating = SecretCipher([new_key, old_key])
    assert rotating.decrypt(old_ciphertext, binding="b") == b"token"
    time.sleep(1)
    kid, rotated = rotating.rotate(old_ciphertext)
    assert kid == "k2026"
    assert SecretCipher([new_key]).decrypt(rotated, binding="b") == b"token"
    old_fernet = Fernet(old_key.key.get_secret_value().encode())
    new_fernet = Fernet(new_key.key.get_secret_value().encode())
    assert new_fernet.extract_timestamp(rotated) == old_fernet.extract_timestamp(old_ciphertext)


def test_cipher_requires_unique_keys() -> None:
    with pytest.raises(ValueError):
        SecretCipher([])
    with pytest.raises(ValueError, match="unique"):
        SecretCipher([key("k"), key("k")])


def test_api_key_mint_and_parse() -> None:
    minted = mint_api_key(environment="local", pepper="pepper")
    assert minted.plaintext.startswith("ghk_local_")
    parsed = parse_api_key(minted.plaintext)
    assert parsed.key_prefix == minted.key_prefix
    assert parsed.environment == "local"
    assert secrets_match(parsed.secret, minted.secret_hash, pepper="pepper")
    assert not secrets_match(parsed.secret, minted.secret_hash, pepper="other")


@pytest.mark.parametrize(
    "value", ["", "ghk_local_short_x", "abc_local_abcdefghijkl_" + "a" * 43, "ghk_local"]
)
def test_malformed_api_keys_rejected(value: str) -> None:
    with pytest.raises(InvalidApiKeyError):
        parse_api_key(value)


def test_session_tokens_are_hashed() -> None:
    token = generate_session_token()
    assert len(token) >= 43
    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != hash_session_token(generate_session_token())


def test_passwords() -> None:
    passwords = PasswordService()
    stored = passwords.hash("correct horse")
    assert stored.startswith("$argon2id$")
    assert passwords.verify(stored, "correct horse")
    assert not passwords.verify(stored, "wrong")
    assert not passwords.verify("not-a-hash", "x")
    with pytest.raises(ValueError):
        passwords.hash("")
