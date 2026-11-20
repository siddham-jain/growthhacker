import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

SYSTEM_PRINCIPAL_ID = uuid.UUID(int=0)


class Role(StrEnum):
    viewer = "viewer"
    editor = "editor"
    owner = "owner"

    @property
    def rank(self) -> int:
        return list(Role).index(self)

    def allows(self, required: "Role") -> bool:
        return self.rank >= required.rank


class PermissionDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class TenantScope:
    workspace_id: uuid.UUID
    client_id: uuid.UUID | None = None

    def for_client(self, client_id: uuid.UUID) -> "TenantScope":
        return TenantScope(workspace_id=self.workspace_id, client_id=client_id)


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "api_key", "system"]
    id: uuid.UUID
    workspace_id: uuid.UUID | None
    role: Role
    scopes: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def system(cls) -> "Principal":
        return cls(kind="system", id=SYSTEM_PRINCIPAL_ID, workspace_id=None, role=Role.owner)

    @property
    def is_system(self) -> bool:
        return self.kind == "system"

    def scope(self, client_id: uuid.UUID | None = None) -> TenantScope:
        if self.workspace_id is None:
            raise PermissionDeniedError("principal is not bound to a workspace")
        return TenantScope(workspace_id=self.workspace_id, client_id=client_id)


def require_role(principal: Principal, required: Role) -> Principal:
    if not principal.role.allows(required):
        raise PermissionDeniedError(f"role '{required}' required, principal has '{principal.role}'")
    return principal


def require_scope(principal: Principal, scope_name: str) -> Principal:
    if principal.kind == "api_key" and scope_name not in principal.scopes and "admin" not in principal.scopes:
        raise PermissionDeniedError(f"api key lacks scope '{scope_name}'")
    return principal
