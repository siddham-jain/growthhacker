import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from app.core.clock import Clock
from app.core.config import GoogleOAuthSettings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class OAuthError(Exception):
    pass


class InvalidGrant(OAuthError):
    # revoked, unused for six months, over the per-account token limit, or a 7-day testing-mode token
    pass


@dataclass(frozen=True)
class TokenSet:
    access_token: SecretStr
    refresh_token: SecretStr | None
    expires_at: datetime
    scopes: list[str]
    subject: str | None


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _subject(id_token: str | None) -> str | None:
    # the id token comes straight from google's token endpoint over tls, so its payload is read without
    # signature checks (openid connect core 3.1.3.7)
    if not id_token or id_token.count(".") != 2:
        return None
    payload = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    return claims.get("sub")


class GoogleOAuthFlow:
    """oauth 2.0 web flow with pkce; tokens never leave this class unencrypted except to the caller."""

    def __init__(self, client: httpx.AsyncClient, settings: GoogleOAuthSettings, clock: Clock) -> None:
        if not settings.client_id or settings.client_secret is None:
            raise OAuthError("google oauth client id and secret are not configured")
        self._client = client
        self._settings = settings
        self._clock = clock

    @property
    def scopes(self) -> list[str]:
        return [*self._settings.scopes, "openid"]

    def authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def _token_request(self, data: dict[str, str]) -> dict:
        payload = {
            "client_id": self._settings.client_id or "",
            "client_secret": self._settings.client_secret.get_secret_value()
            if self._settings.client_secret
            else "",
            **data,
        }
        try:
            response = await self._client.post(TOKEN_URL, data=payload, timeout=15)
        except httpx.TransportError as error:
            raise OAuthError(f"token endpoint unreachable: {type(error).__name__}") from None
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            if body.get("error") == "invalid_grant":
                raise InvalidGrant(body.get("error_description") or "invalid_grant")
            raise OAuthError(f"token endpoint returned {response.status_code}: {body.get('error')}")
        return body

    def _token_set(self, body: dict, refresh_token: SecretStr | None = None) -> TokenSet:
        expires_in = int(body.get("expires_in") or 3600)
        fresh_refresh = body.get("refresh_token")
        return TokenSet(
            access_token=SecretStr(body["access_token"]),
            refresh_token=SecretStr(fresh_refresh) if fresh_refresh else refresh_token,
            expires_at=self._clock.now() + timedelta(seconds=expires_in),
            scopes=str(body.get("scope") or "").split(),
            subject=_subject(body.get("id_token")),
        )

    async def exchange(self, code: str, code_verifier: str) -> TokenSet:
        body = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": self._settings.redirect_uri,
            }
        )
        return self._token_set(body)

    async def refresh(self, refresh_token: SecretStr) -> TokenSet:
        body = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token.get_secret_value()}
        )
        return self._token_set(body, refresh_token)

    async def revoke(self, token: SecretStr) -> bool:
        try:
            response = await self._client.post(
                REVOKE_URL, data={"token": token.get_secret_value()}, timeout=15
            )
        except httpx.TransportError:
            return False
        # 400 means google already considers the token invalid, which is the outcome we want
        return response.status_code in (200, 400)
