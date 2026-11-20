import base64
import hashlib
import hmac
import json
import re
import secrets
import string
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import SecurityKey

API_KEY_PREFIX_LENGTH = 12
API_KEY_PATTERN = re.compile(
    r"^(?P<brand>[a-z]+)_(?P<env>[a-z]+)_(?P<prefix>[a-z0-9]{12})_(?P<secret>[A-Za-z0-9_-]{43})$"
)
_PREFIX_ALPHABET = string.ascii_lowercase + string.digits


class InvalidCiphertextError(ValueError):
    pass


class InvalidBindingError(ValueError):
    pass


class InvalidApiKeyError(ValueError):
    pass


class SecretCipher:
    """multifernet with a row binding inside the plaintext, since fernet has no associated data."""

    def __init__(self, keys: list[SecurityKey]) -> None:
        if not keys:
            raise ValueError("at least one token encryption key is required")
        kids = [key.kid for key in keys]
        if len(set(kids)) != len(kids):
            raise ValueError("token encryption key ids must be unique")
        self.primary_kid = keys[0].kid
        self._fernet = MultiFernet([Fernet(key.key.get_secret_value().encode()) for key in keys])

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: bytes, *, binding: str) -> tuple[str, bytes]:
        envelope = {"binding": binding, "payload": base64.b64encode(plaintext).decode()}
        return self.primary_kid, self._fernet.encrypt(json.dumps(envelope).encode())

    def decrypt(self, ciphertext: bytes, *, binding: str) -> bytes:
        try:
            envelope = json.loads(self._fernet.decrypt(ciphertext))
        except InvalidToken as error:
            raise InvalidCiphertextError("ciphertext cannot be decrypted with the configured keys") from error
        if not hmac.compare_digest(envelope.get("binding", ""), binding):
            raise InvalidBindingError("ciphertext belongs to a different row")
        return base64.b64decode(envelope["payload"])

    def rotate(self, ciphertext: bytes) -> tuple[str, bytes]:
        try:
            return self.primary_kid, self._fernet.rotate(ciphertext)
        except InvalidToken as error:
            raise InvalidCiphertextError("ciphertext cannot be decrypted with the configured keys") from error


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def hash_secret(secret: str, *, pepper: str) -> bytes:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).digest()


def secrets_match(secret: str, stored_hash: bytes, *, pepper: str) -> bool:
    return hmac.compare_digest(hash_secret(secret, pepper=pepper), stored_hash)


@dataclass(frozen=True)
class MintedApiKey:
    plaintext: str
    key_prefix: str
    secret_hash: bytes
    environment: str


@dataclass(frozen=True)
class ParsedApiKey:
    key_prefix: str
    secret: str
    environment: str


def mint_api_key(*, environment: str, pepper: str, brand: str = "ghk") -> MintedApiKey:
    if not environment.isalpha() or not environment.islower():
        raise ValueError("api key environment must be lowercase letters only")
    prefix = "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(API_KEY_PREFIX_LENGTH))
    secret = secrets.token_urlsafe(32)
    key_prefix = f"{brand}_{environment}_{prefix}"
    return MintedApiKey(
        plaintext=f"{key_prefix}_{secret}",
        key_prefix=key_prefix,
        secret_hash=hash_secret(secret, pepper=pepper),
        environment=environment,
    )


def parse_api_key(value: str, *, brand: str = "ghk") -> ParsedApiKey:
    match = API_KEY_PATTERN.match(value.strip())
    if match is None or match["brand"] != brand:
        raise InvalidApiKeyError("malformed api key")
    return ParsedApiKey(
        key_prefix=f"{match['brand']}_{match['env']}_{match['prefix']}",
        secret=match["secret"],
        environment=match["env"],
    )


class PasswordService:
    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self._hasher = hasher or PasswordHasher()

    def hash(self, password: str) -> str:
        if not password:
            raise ValueError("password must not be empty")
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
