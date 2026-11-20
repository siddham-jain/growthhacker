"""demo bootstrap: one workspace, an owner and a first api key (printed once).

uv run python -m app.api.cli bootstrap --workspace "Demo" --email owner@example.com --password '…' --auto-confirm
"""

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.container import build_container
from app.core.security import PasswordService, mint_api_key
from app.models.tenancy import ApiKey, User, Workspace, WorkspaceMember

ADMIN_SCOPES = ["admin"]
_SLUG_INVALID = re.compile(r"[^a-z0-9]+")


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapResult:
    workspace_id: str
    user_id: str
    api_key: str
    created_user: bool


async def bootstrap(
    settings: Settings,
    *,
    workspace_name: str,
    email: str,
    password: str,
    auto_confirm: bool,
    key_name: str = "bootstrap",
) -> BootstrapResult:
    pepper = settings.security.hash_pepper
    if pepper is None:
        raise BootstrapError("set GH_SECURITY__HASH_PEPPER so the api key can be hashed")
    if len(password) < 10:
        raise BootstrapError("use a password of at least 10 characters")
    passwords = PasswordService()
    minted = mint_api_key(
        environment=settings.app.env, pepper=pepper.get_secret_value(), brand=settings.security.api_key_prefix
    )
    container = build_container(settings)
    try:
        async with container.session_factory() as session, session.begin():
            slug = _SLUG_INVALID.sub("-", workspace_name.lower()).strip("-") or "workspace"
            workspace = (
                await session.execute(select(Workspace).where(Workspace.slug == slug))
            ).scalar_one_or_none()
            if workspace is None:
                workspace = Workspace(name=workspace_name, slug=slug, settings={})
                session.add(workspace)
                await session.flush()
            workspace.settings = {**(workspace.settings or {}), "auto_confirm_setup": auto_confirm}
            user = (
                await session.execute(select(User).where(User.email == email.lower()))
            ).scalar_one_or_none()
            created_user = user is None
            if user is None:
                user = User(email=email.lower(), password_hash=passwords.hash(password))
                session.add(user)
                await session.flush()
            membership = await session.get(WorkspaceMember, (workspace.id, user.id))
            if membership is None:
                session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
            session.add(
                ApiKey(
                    workspace_id=workspace.id,
                    name=key_name,
                    environment=minted.environment,
                    key_prefix=minted.key_prefix,
                    secret_hash=minted.secret_hash,
                    scopes=ADMIN_SCOPES,
                    created_by_user_id=user.id,
                )
            )
            workspace_id, user_id = workspace.id, user.id
    finally:
        await container.aclose()
    return BootstrapResult(
        workspace_id=f"ws_{workspace_id.hex}",
        user_id=f"usr_{user_id.hex}",
        api_key=minted.plaintext,
        created_user=created_user,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.api.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    boot = commands.add_parser("bootstrap", help="create a workspace, an owner and an api key")
    boot.add_argument("--workspace", required=True)
    boot.add_argument("--email", required=True)
    boot.add_argument("--password", required=True)
    boot.add_argument("--auto-confirm", action="store_true", help="demo mode: accept onboarding suggestions")
    boot.add_argument("--key-name", default="bootstrap")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            bootstrap(
                get_settings(),
                workspace_name=args.workspace,
                email=args.email,
                password=args.password,
                auto_confirm=args.auto_confirm,
                key_name=args.key_name,
            )
        )
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"workspace {result.workspace_id}")
    print(f"user      {result.user_id}{' (created)' if result.created_user else ' (existing)'}")
    print(f"api key   {result.api_key}")
    print("the api key is shown once; store it now")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
