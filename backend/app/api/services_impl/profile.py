import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import FieldError, NotFound, PreconditionFailed, ValidationFailed
from app.api.schemas.setup import BusinessProfileView, ProfileSource
from app.api.services import PROFILE, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile, ClaimMeta, ClaimOrigin, ClaimStatus
from app.models.tenancy import Client
from app.models.website import BusinessProfile as ProfileRow
from app.models.website import ProfileEdit
from app.pipeline.client_settings import merged, read_settings, write_settings
from app.services.topics.engine import TopicEngine
from app.services.website_intel.persistence import WebsiteIntelStore
from app.services.website_intel.profile.merger import MERGEABLE, EditOp, ProfileEditor, Tombstone

USER = "user"
IDENTITY_FIELDS = {"name": "name_meta", "one_liner": "one_liner_meta", "industry": "industry_meta"}
# collections 05 reads when it builds the client's topic universe
TOPIC_SOURCES = frozenset({"offerings", "niche_keywords", "brand_terms"})
PROTECTED_FIELDS = frozenset({"meta"})


class ProfileMissing(NotFound):
    code = "profile_not_ready"
    title = "No business profile has been built for this client yet"


def invalid(message: str, field: str) -> ValidationFailed:
    return ValidationFailed(message, errors=[FieldError(loc=["body", field], msg=message, type="invalid")])


@dataclass(frozen=True)
class LoadedProfile:
    effective: BusinessProfile
    status: str
    machine: BusinessProfile
    overrides: dict[str, Any]
    tombstones: list[Tombstone]


def keep_nulls_merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    # unlike rfc 7386, null is kept: in overrides it records a rejection that must outlive re-crawls
    merged_value = dict(target)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged_value.get(key), dict):
            merged_value[key] = keep_nulls_merge(merged_value[key], value)
        else:
            merged_value[key] = value
    return merged_value


class ProfileEdits:
    """03's immutable profile versions: user edits become a new version with locks and tombstones."""

    def __init__(self, container: Container) -> None:
        self.container = container
        self.editor = ProfileEditor()
        self._topics: TopicEngine | None = None

    async def load(self, session: AsyncSession, scope: TenantScope) -> LoadedProfile:
        rows = (
            await session.execute(
                select(ProfileRow)
                .where(ProfileRow.workspace_id == scope.workspace_id, ProfileRow.client_id == scope.client_id)
                .order_by(ProfileRow.version.desc())
            )
        ).scalars()
        rows = list(rows)
        if not rows:
            raise ProfileMissing("the business profile is still being built")
        latest = rows[0]
        machine = next((row for row in rows if row.created_by != USER), latest)
        edits = (
            await session.execute(
                select(ProfileEdit.ops)
                .where(
                    ProfileEdit.workspace_id == scope.workspace_id, ProfileEdit.client_id == scope.client_id
                )
                .order_by(ProfileEdit.created_at)
            )
        ).scalars()
        overrides: dict[str, Any] = {}
        for ops in edits:
            for patch in ops:
                if isinstance(patch, dict):
                    overrides = keep_nulls_merge(overrides, patch)
        store = WebsiteIntelStore(session, scope)
        return LoadedProfile(
            effective=BusinessProfile.model_validate(latest.profile),
            status=latest.status,
            machine=BusinessProfile.model_validate(machine.profile),
            overrides=overrides,
            tombstones=await store.tombstones(),
        )

    def ops_for(self, profile: BusinessProfile, patch: dict[str, Any]) -> tuple[list[EditOp], dict[str, Any]]:
        """a json merge patch keyed by item id: an object edits or adds, status confirms, null rejects."""
        ops: list[EditOp] = []
        identity: dict[str, Any] = {}
        for collection, changes in patch.items():
            if collection == "identity":
                identity = self._identity_changes(changes)
                continue
            if collection not in MERGEABLE:
                editable = ", ".join(["identity", *MERGEABLE])
                raise invalid(f"'{collection}' cannot be edited; editable fields: {editable}", collection)
            if not isinstance(changes, dict) or not changes:
                raise invalid(f"'{collection}' must map item ids to changes", collection)
            key = MERGEABLE[collection][0]
            existing = {str(getattr(item, key)) for item in getattr(profile, collection)}
            for item_id, change in changes.items():
                ops.append(self._op(collection, key, item_id, change, item_id in existing))
        return ops, identity

    @staticmethod
    def _identity_changes(changes: Any) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise invalid("identity must be an object of field edits", "identity")
        unknown = sorted(set(changes) - set(IDENTITY_FIELDS))
        if unknown or any(not isinstance(value, str) or not value.strip() for value in changes.values()):
            raise invalid(f"identity accepts text edits of {', '.join(IDENTITY_FIELDS)}", "identity")
        return changes

    @staticmethod
    def _op(collection: str, key: str, item_id: str, change: Any, exists: bool) -> EditOp:
        location = f"{collection}.{item_id}"
        if change is None:
            if not exists:
                raise invalid(f"{location} does not exist", location)
            return EditOp(op="reject", collection=collection, item_id=item_id)
        if not isinstance(change, dict):
            raise invalid(f"{location} must be an object or null", location)
        fields = {name: value for name, value in change.items() if name != "status"}
        status = change.get("status")
        if status not in (None, "confirmed", "rejected"):
            raise invalid("status may be 'confirmed' or 'rejected'", location)
        if PROTECTED_FIELDS & set(fields) or key in fields:
            raise invalid(f"{location}: '{key}' and 'meta' are managed by GrowthHacker", location)
        if not exists:
            if status == "rejected":
                raise invalid(f"{location} does not exist", location)
            return EditOp(op="add", collection=collection, item={key: item_id, **fields})
        if status == "rejected":
            return EditOp(op="reject", collection=collection, item_id=item_id)
        if fields:
            return EditOp(op="edit", collection=collection, item_id=item_id, fields=fields)
        if status == "confirmed":
            return EditOp(op="confirm", collection=collection, item_id=item_id)
        raise invalid(f"{location} has nothing to change", location)

    async def commit(
        self,
        session: AsyncSession,
        scope: TenantScope,
        loaded: LoadedProfile,
        ops: list[EditOp],
        identity: dict[str, Any],
        patch: dict[str, Any],
        now: datetime,
    ) -> BusinessProfile:
        base = loaded.effective
        try:
            edited, stones = self.editor.apply(base, ops, now=now)
        except KeyError as error:
            raise invalid(str(error).strip("'\""), "body") from None
        except (ValueError, ValidationError) as error:
            raise invalid(str(error)[:300], "body") from None
        if identity:
            edited = edited.model_copy(update={"identity": self._edit_identity(edited, identity, now)})
        try:
            profile = BusinessProfile.model_validate(
                {
                    **edited.model_dump(mode="json"),
                    "profile_id": str(uuid.uuid4()),
                    "status": loaded.status if loaded.status != "superseded" else "confirmed",
                }
            )
        except ValidationError as error:
            raise invalid(str(error)[:300], "body") from None
        store = WebsiteIntelStore(session, scope)
        await session.execute(
            update(ProfileRow)
            .where(
                ProfileRow.workspace_id == scope.workspace_id,
                ProfileRow.client_id == scope.client_id,
                ProfileRow.status == "confirmed",
            )
            .values(status="superseded")
        )
        await store.save_profile(profile, created_by=USER, llm_usage={}, prompt_versions={})
        await store.add_tombstones(stones)
        session.add(
            ProfileEdit(
                workspace_id=scope.workspace_id,
                client_id=scope.client_id,
                base_version=base.version,
                result_version=profile.version,
                ops=[patch],
            )
        )
        client = await session.get(Client, scope.client_id, with_for_update={"key_share": True})
        if client is not None:
            settings = read_settings(client)
            write_settings(client, merged(settings, config_version=settings.config_version + 1))
        return profile

    @staticmethod
    def _edit_identity(profile: BusinessProfile, changes: dict[str, Any], now: datetime):
        identity = profile.identity
        update_values: dict[str, Any] = {}
        for field, value in changes.items():
            meta_field = IDENTITY_FIELDS[field]
            meta: ClaimMeta = getattr(identity, meta_field)
            update_values[field] = value.strip()
            update_values[meta_field] = meta.model_copy(
                update={
                    "status": ClaimStatus.edited,
                    "origin": ClaimOrigin.user,
                    "locked": True,
                    "updated_at": now,
                }
            )
        return identity.model_copy(update=update_values)

    def topic_engine(self) -> TopicEngine:
        # lexical expansion only: an edit must not spend llm budget or wait on the embedder
        if self._topics is None:
            container = self.container
            self._topics = TopicEngine(
                container.settings, container.clock, container.uow, router=None, llm=None
            )
        return self._topics

    async def rebuild_topics(self, scope: TenantScope) -> None:
        """the next run's snapshot reads the newest topic universe, so edits apply without a re-crawl."""
        async with self.container.session_factory() as session:
            loaded = await self.load(session, scope)
            questions = await WebsiteIntelStore(session, scope).buyer_questions()
        await self.topic_engine().build_universe(scope, loaded.effective, questions)


def profile_sources(profile: BusinessProfile) -> list[ProfileSource]:
    claims: Counter[str] = Counter()
    first: dict[str, Any] = {}
    collections = [getattr(profile, name) for name in MERGEABLE] + [profile.locations.items]
    metas = [item.meta for items in collections for item in items]
    metas += [profile.identity.one_liner_meta, profile.freshness.meta]
    for meta in metas:
        for evidence in meta.evidence:
            claims[evidence.source_url] += 1
            first.setdefault(evidence.source_url, evidence)
    return [
        ProfileSource(
            url=url,
            source_kind=first[url].source_kind,
            fetched_at=first[url].fetched_at,
            snapshot_at=first[url].snapshot_at,
            claims=count,
        )
        for url, count in sorted(claims.items(), key=lambda item: (-item[1], item[0]))
    ]


class ProfileReadService(ServiceBase):
    def __init__(self, container: Container) -> None:
        super().__init__(container)
        self.edits = ProfileEdits(container)

    def view(self, loaded: LoadedProfile, effective: BusinessProfile | None = None) -> BusinessProfileView:
        profile = effective or loaded.effective
        return BusinessProfileView(
            profile=loaded.machine,
            overrides=loaded.overrides,
            effective=profile,
            version=profile.version,
            completeness=profile.quality.completeness,
            partial=profile.quality.thin_site or bool(profile.coverage.blocked),
            sources=profile_sources(profile),
        )

    async def get(self, scope: TenantScope) -> BusinessProfileView:
        async with self.session_factory() as session:
            return self.view(await self.edits.load(session, scope))

    async def patch(
        self, scope: TenantScope, merge_patch: dict[str, Any], expected_version: int
    ) -> BusinessProfileView:
        if not merge_patch:
            raise invalid("send at least one change", "body")
        async with self.session_factory() as session, session.begin():
            loaded = await self.edits.load(session, scope)
            if loaded.effective.version != expected_version:
                raise PreconditionFailed(
                    f"the profile is at version {loaded.effective.version}",
                    extra={"version": loaded.effective.version},
                )
            ops, identity = self.edits.ops_for(loaded.effective, merge_patch)
            await self.edits.commit(session, scope, loaded, ops, identity, merge_patch, self.clock.now())
        if TOPIC_SOURCES & set(merge_patch):
            await self.edits.rebuild_topics(scope)
        return await self.get(scope)


@api_service_registry.register(PROFILE)
def build_profile_service(container: Container) -> ProfileReadService:
    return ProfileReadService(container)
