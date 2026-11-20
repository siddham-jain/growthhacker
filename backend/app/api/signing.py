import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SignatureInvalid(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Signer:
    """hmac-signed opaque tokens (pagination cursors, sse stream tokens); one sub-key per purpose."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    @classmethod
    def from_settings(cls, settings: Settings) -> "Signer":
        configured = settings.api.signing_key or settings.security.hash_pepper
        if configured is not None:
            return cls(configured.get_secret_value().encode())
        if settings.app.env in {"staging", "prod"}:
            raise RuntimeError("set GH_API__SIGNING_KEY or GH_SECURITY__HASH_PEPPER before starting the api")
        logger.warning("api.ephemeral_signing_key", detail="cursors and stream tokens reset on restart")
        return cls(secrets.token_bytes(32))

    def _key(self, purpose: str) -> bytes:
        return hmac.new(self._secret, purpose.encode(), hashlib.sha256).digest()

    def sign(self, purpose: str, payload: dict[str, Any]) -> str:
        body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self._key(purpose), body.encode(), hashlib.sha256).digest()[:18])
        return f"{body}.{signature}"

    def verify(self, purpose: str, token: str) -> dict[str, Any]:
        body, separator, signature = token.partition(".")
        if not separator:
            raise SignatureInvalid("malformed token")
        expected = _b64(hmac.new(self._key(purpose), body.encode(), hashlib.sha256).digest()[:18])
        if not hmac.compare_digest(expected, signature):
            raise SignatureInvalid("bad signature")
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, UnicodeDecodeError) as error:
            raise SignatureInvalid("malformed token") from error
        if not isinstance(payload, dict):
            raise SignatureInvalid("malformed token")
        return payload
