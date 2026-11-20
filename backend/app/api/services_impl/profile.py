import re
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import FieldError, NotFound, PreconditionFailed, ValidationFailed
from app.api.schemas.setup import BusinessProfileView, ProfileSource
from app.api.services import PROFILE, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile, ClaimMeta, ClaimOrigin, ClaimStatus, Location, LocationSummary
from app.models.tenancy import Client
from app.models.website import BusinessProfile as ProfileRow
from app.models.website import ProfileEdit, ProfileTombstone
from app.pipeline.client_settings import merged, read_settings, write_settings
from app.services.topics.engine import TopicEngine
from app.services.website_intel.persistence import WebsiteIntelStore
from app.services.website_intel.profile.claims import normalise_name
from app.services.website_intel.profile.market import market_timezone
from app.services.website_intel.profile.merger import KEYED, MERGEABLE, EditOp, ProfileEditor, Tombstone

USER = "user"
# identity field -> its claim meta; the country carries none and also sets the client's market
IDENTITY_FIELDS = {
    "name": "name_meta",
    "one_liner": "one_liner_meta",
    "industry": "industry_meta",
    "country": None,
}
COUNTRY = re.compile(r"^[A-Z]{2}$")
# collections 05 reads when it builds the client's topic universe
TOPIC_SOURCES = frozenset({"offerings", "niche_keywords", "brand_terms"})
PROTECTED_FIELDS = frozenset({"meta"})
# locations live inside the location summary, which 03's editor does not address, so they are edited here
LOCATIONS = "locations"
PHONE = re.compile(r"^\+?[\d\s().-]{7,20}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NO_PROMINENCE = {"pages_seen": 0, "on_home": False, "in_header": False, "in_footer": False}


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


def contact_target(label: str) -> dict[str, str] | None:
    """a label that is itself a phone number, email or link says where the action goes."""
    text = label.strip()
    if EMAIL.match(text):
        return {"type": "email", "medium": "email", "target": f"mailto:{text}"}
    if PHONE.match(text) and sum(char.isdigit() for char in text) >= 7:
        return {"type": "call", "medium": "phone", "target": "tel:" + re.sub(r"[^\d+]", "", text)}
    if text.startswith(("http://", "https://")):
        return {"type": "other", "medium": "web", "target": text}
    return None


def contact_defaults(fields: dict[str, Any]) -> dict[str, Any]:
    label = str(fields.get("label", ""))
    target = contact_target(label) or {"type": "other", "medium": "web", "target": label}
    return {**target, "prominence": NO_PROMINENCE}


def kind_other(fields: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "other"}


# a user adds an item as one line of text; these fill the fields that line cannot carry
ITEM_DEFAULTS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "offerings": kind_other,
    "value_propositions": kind_other,
    "target_customers": kind_other,
    "conversion_actions": contact_defaults,
    LOCATIONS: kind_other,
}


def stone_key(collection: str, item: dict[str, Any]) -> tuple[str, str]:
    key, text = KEYED[collection]
    value = str(item.get(key, ""))
    return (normalise_name(value) if key in {"term", "name"} else value), str(item.get(text, ""))


def edit_locations(
    profile: BusinessProfile, ops: Sequence[EditOp], now: datetime
) -> tuple[BusinessProfile, list[Tombstone]]:
    if not ops:
        return profile, []
    summary = profile.locations
    items = list(summary.items)
    stones: list[Tombstone] = []
    for op in ops:
        if op.op == "add":
            meta = ClaimMeta(
                origin=ClaimOrigin.user,
                status=ClaimStatus.confirmed,
                confidence=1.0,
                locked=True,
                first_seen_version=profile.version,
                updated_at=now,
            )
            items.append(Location.model_validate({**(op.item or {}), "meta": meta}))
            continue
        index = next((i for i, item in enumerate(items) if item.id == op.item_id), None)
        if index is None:
            raise invalid(f"locations.{op.item_id} does not exist", f"locations.{op.item_id}")
        item = items[index]
        if op.op == "reject":
            stones.append(Tombstone(LOCATIONS, item.id, item.name))
            items.pop(index)
            continue
        status = ClaimStatus.confirmed if op.op == "confirm" else ClaimStatus.edited
        edited = item.model_copy(update=op.fields or {})
        items[index] = edited.model_copy(
            update={
                "meta": item.meta.model_copy(update={"status": status, "locked": True, "updated_at": now})
            }
        )
    return profile.model_copy(update={"locations": location_summary(summary, items)}), stones


def location_summary(summary: LocationSummary, items: list[Location]) -> LocationSummary:
    kept = {item.city for item in items if item.city}
    dropped = {item.city for item in summary.items if item.city} - kept
    cities = [city for city in summary.cities if city not in dropped]
    cities += sorted(city for city in kept if city not in cities)
    return summary.model_copy(
        update={
            "items": items,
            "count": max(len(items), summary.count + len(items) - len(summary.items)),
            "cities": cities,
        }
    )


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

    @staticmethod
    async def lock(session: AsyncSession, scope: TenantScope) -> None:
        # edits read the newest version and write the next one, so two at once must queue on the client row
        await session.get(Client, scope.client_id, with_for_update={"key_share": True})

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
            if collection not in KEYED:
                editable = ", ".join(["identity", *KEYED])
                raise invalid(f"'{collection}' cannot be edited; editable fields: {editable}", collection)
            if not isinstance(changes, dict) or not changes:
                raise invalid(f"'{collection}' must map item ids to changes", collection)
            key = KEYED[collection][0]
            items = profile.locations.items if collection == LOCATIONS else getattr(profile, collection)
            existing = {str(getattr(item, key)) for item in items}
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
        if "country" in changes and not COUNTRY.match(changes["country"].strip().upper()):
            raise invalid("country must be a two-letter region code such as IN", "identity.country")
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
            defaults = ITEM_DEFAULTS.get(collection)
            item = {**(defaults(fields) if defaults else {}), **fields}
            return EditOp(op="add", collection=collection, item={key: item_id, **item})
        if status == "rejected":
            return EditOp(op="reject", collection=collection, item_id=item_id)
        if fields:
            if collection == "conversion_actions" and "label" in fields and "target" not in fields:
                fields = {**(contact_target(str(fields["label"])) or {}), **fields}
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
        location_ops = [op for op in ops if op.collection == LOCATIONS]
        try:
            edited, stones = self.editor.apply(
                base, [op for op in ops if op.collection != LOCATIONS], now=now
            )
            edited, location_stones = edit_locations(edited, location_ops, now)
        except KeyError as error:
            raise invalid(str(error).strip("'\""), "body") from None
        except (ValueError, ValidationError) as error:
            raise invalid(str(error)[:300], "body") from None
        stones += location_stones
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
        await self.lift_tombstones(session, scope, ops)
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
            changes: dict[str, Any] = {"config_version": settings.config_version + 1}
            if country := profile.identity.country if "country" in identity else None:
                # the user's market wins over what later crawls infer from the site
                client.country = country
                client.timezone = market_timezone(country, client.timezone) or client.timezone
                changes.update(market_explicit=True, version=settings.version + 1)
            write_settings(client, merged(settings, **changes))
        return profile

    @staticmethod
    async def lift_tombstones(session: AsyncSession, scope: TenantScope, ops: Sequence[EditOp]) -> None:
        # adding back a rejected item (an undo) lifts its tombstone so the next crawl may keep it
        for op in ops:
            if op.op != "add" or not op.item:
                continue
            key, text = stone_key(op.collection, op.item)
            await session.execute(
                delete(ProfileTombstone).where(
                    ProfileTombstone.workspace_id == scope.workspace_id,
                    ProfileTombstone.client_id == scope.client_id,
                    ProfileTombstone.collection == op.collection,
                    or_(ProfileTombstone.item_key == key, ProfileTombstone.text == text),
                )
            )

    @staticmethod
    def _edit_identity(profile: BusinessProfile, changes: dict[str, Any], now: datetime):
        identity = profile.identity
        update_values: dict[str, Any] = {}
        for field, value in changes.items():
            meta_field = IDENTITY_FIELDS[field]
            if meta_field is None:
                update_values[field] = value.strip().upper()
                continue
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
                container.settings,
                container.clock,
                container.uow,
                router=None,
                llm=None,
                locks=container.lock_manager,
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
            await self.edits.lock(session, scope)
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
