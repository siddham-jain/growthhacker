from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text, update

from app.api.errors import Forbidden, InvalidCredentials
from app.core.config import Settings
from app.core.db import SessionFactory
from app.core.security import (
    InvalidApiKeyError,
    PasswordService,
    generate_session_token,
    hash_session_token,
    parse_api_key,
    secrets_match,
)
from app.core.tenancy import Principal, Role
from app.models.tenancy import ApiKey, User, UserSession, Workspace, WorkspaceMember

TOUCH_INTERVAL = timedelta(minutes=5)
WRITE_SCOPES = frozenset({"clients:write", "runs:write"})
# verifying against a real hash keeps unknown-email logins as slow as wrong-password ones
_DUMMY_HASH = PasswordService().hash("not-a-real-password")


@dataclass(frozen=True)
class AuthContext:
    principal: Principal
    user_id: UUID | None = None
    session_id: UUID | None = None


@dataclass(frozen=True)
class IssuedSession:
    token: str
    expires_at: datetime


def required_scope(method: str, route_path: str) -> str:
    """the api key scope a request needs: read for safe methods, runs:write or clients:write for writes."""
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    return "runs:write" if "/runs" in route_path else "clients:write"


def scope_allows(scopes: frozenset[str], required: str) -> bool:
    # admin covers everything and any write scope also reads
    if "admin" in scopes or required in scopes:
        return True
    return required == "read" and bool(WRITE_SCOPES & scopes)


def role_for_scopes(scopes: list[str]) -> Role:
    if "admin" in scopes:
        return Role.owner
    if WRITE_SCOPES & set(scopes):
        return Role.editor
    return Role.viewer


class Authenticator:
    """api keys (hmac with server pepper) and opaque session tokens (sha256 stored)."""

    def __init__(
        self, session_factory: SessionFactory, settings: Settings, passwords: PasswordService
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._passwords = passwords

    async def api_key(self, token: str) -> AuthContext:
        pepper = self._settings.security.hash_pepper
        if pepper is None:
            raise InvalidCredentials("api keys are disabled until GH_SECURITY__HASH_PEPPER is set")
        try:
            parsed = parse_api_key(token, brand=self._settings.security.api_key_prefix)
        except InvalidApiKeyError:
            raise InvalidCredentials("the api key is malformed") from None
        async with self._session_factory() as session, session.begin():
            now = await _now(session)
            row = (
                await session.execute(
                    select(ApiKey, Workspace.deleted_at)
                    .join(Workspace, Workspace.id == ApiKey.workspace_id)
                    .where(ApiKey.key_prefix == parsed.key_prefix)
                )
            ).one_or_none()
            key = row[0] if row is not None else None
            if key is None or not secrets_match(
                parsed.secret, key.secret_hash, pepper=pepper.get_secret_value()
            ):
                raise InvalidCredentials("the api key is not valid")
            if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
                raise InvalidCredentials("the api key was revoked or has expired")
            if row[1] is not None:
                raise InvalidCredentials("the api key's workspace was deleted")
            creator_role = await self._creator_role(session, key)
            if creator_role is None:
                # committed before refusing, so the key stays dead even if the creator rejoins
                await session.execute(update(ApiKey).where(ApiKey.id == key.id).values(revoked_at=now))
            elif key.last_used_at is None or now - key.last_used_at > TOUCH_INTERVAL:
                await session.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        if creator_role is None:
            raise InvalidCredentials("the api key was revoked because its creator left the workspace")
        scopes = list(key.scopes or [])
        key_role = role_for_scopes(scopes)
        principal = Principal(
            kind="api_key",
            id=key.id,
            workspace_id=key.workspace_id,
            # a key never outranks the member who created it
            role=key_role if creator_role.allows(key_role) else creator_role,
            scopes=frozenset(scopes),
        )
        return AuthContext(principal=principal, user_id=key.created_by_user_id)

    @staticmethod
    async def _creator_role(session, key: ApiKey) -> Role | None:
        if key.created_by_user_id is None:
            return None
        role = (
            await session.execute(
                select(WorkspaceMember.role)
                .join(User, User.id == WorkspaceMember.user_id)
                .where(
                    WorkspaceMember.user_id == key.created_by_user_id,
                    WorkspaceMember.workspace_id == key.workspace_id,
                    User.disabled_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return None if role is None else Role(role)

    async def session(self, token: str) -> AuthContext:
        idle = timedelta(hours=self._settings.security.session_idle_ttl_h)
        async with self._session_factory() as session, session.begin():
            now = await _now(session)
            row = (
                await session.execute(
                    select(UserSession, User)
                    .join(User, User.id == UserSession.user_id)
                    .where(UserSession.token_hash == hash_session_token(token))
                )
            ).one_or_none()
            if row is None:
                raise InvalidCredentials("the session is not valid")
            user_session, user = row
            if (
                user_session.revoked_at is not None
                or user_session.expires_at <= now
                or user_session.idle_expires_at <= now
                or user.disabled_at is not None
            ):
                raise InvalidCredentials("the session has expired")
            membership = await self._membership(session, user.id, user_session.active_workspace_id)
            if now - user_session.last_seen_at > TOUCH_INTERVAL:
                await session.execute(
                    update(UserSession)
                    .where(UserSession.id == user_session.id)
                    .values(last_seen_at=now, idle_expires_at=min(now + idle, user_session.expires_at))
                )
        workspace_id, role = membership if membership is not None else (None, Role.viewer)
        principal = Principal(kind="user", id=user.id, workspace_id=workspace_id, role=role)
        return AuthContext(principal=principal, user_id=user.id, session_id=user_session.id)

    async def login(self, email: str, password: str, *, user_agent: str | None) -> IssuedSession:
        security = self._settings.security
        async with self._session_factory() as session, session.begin():
            user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None or user.password_hash is None:
                self._passwords.verify(_DUMMY_HASH, password)
                raise InvalidCredentials("email or password is wrong")
            if not self._passwords.verify(user.password_hash, password) or user.disabled_at is not None:
                raise InvalidCredentials("email or password is wrong")
            now = await _now(session)
            membership = await self._membership(session, user.id, None)
            token = generate_session_token()
            expires_at = now + timedelta(hours=security.session_ttl_h)
            session.add(
                UserSession(
                    user_id=user.id,
                    active_workspace_id=None if membership is None else membership[0],
                    token_hash=hash_session_token(token),
                    last_seen_at=now,
                    expires_at=expires_at,
                    idle_expires_at=min(now + timedelta(hours=security.session_idle_ttl_h), expires_at),
                    user_agent=(user_agent or "")[:300] or None,
                )
            )
            values = {"last_login_at": now}
            if self._passwords.needs_rehash(user.password_hash):
                values["password_hash"] = self._passwords.hash(password)
            await session.execute(update(User).where(User.id == user.id).values(**values))
        return IssuedSession(token=token, expires_at=expires_at)

    async def logout(self, session_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(UserSession)
                .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
                .values(revoked_at=func.now())
            )

    async def switch_workspace(self, context: AuthContext, workspace_id: UUID) -> None:
        if context.session_id is None or context.user_id is None:
            raise Forbidden("only browser sessions switch workspaces")
        async with self._session_factory() as session, session.begin():
            if await self._membership(session, context.user_id, workspace_id, strict=True) is None:
                raise Forbidden("you are not a member of that workspace")
            await session.execute(
                update(UserSession)
                .where(UserSession.id == context.session_id)
                .values(active_workspace_id=workspace_id)
            )

    async def workspaces(self, user_id: UUID) -> list[tuple[Workspace, Role]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Workspace, WorkspaceMember.role)
                    .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                    .where(WorkspaceMember.user_id == user_id, Workspace.deleted_at.is_(None))
                    .order_by(Workspace.created_at)
                )
            ).all()
        return [(workspace, Role(role)) for workspace, role in rows]

    async def user(self, user_id: UUID) -> User | None:
        async with self._session_factory() as session:
            return await session.get(User, user_id)

    @staticmethod
    async def _membership(
        session, user_id: UUID, workspace_id: UUID | None, *, strict: bool = False
    ) -> tuple[UUID, Role] | None:
        statement = (
            select(WorkspaceMember.workspace_id, WorkspaceMember.role)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user_id, Workspace.deleted_at.is_(None))
        )
        if workspace_id is not None:
            row = (
                await session.execute(statement.where(WorkspaceMember.workspace_id == workspace_id))
            ).first()
            if row is not None or strict:
                return None if row is None else (row[0], Role(row[1]))
        row = (await session.execute(statement.order_by(WorkspaceMember.created_at))).first()
        return None if row is None else (row[0], Role(row[1]))


async def _now(session) -> datetime:
    return (await session.execute(text("SELECT now()"))).scalar_one()
