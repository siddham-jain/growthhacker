from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

from app.domain.profile import BusinessProfile, ClaimMeta, ClaimOrigin, ClaimStatus
from app.services.website_intel.profile.claims import normalise_name

TOMBSTONE_MATCH = 90
# collection -> (attribute holding the item key, attribute holding comparable text)
MERGEABLE: dict[str, tuple[str, str]] = {
    "offerings": ("id", "name"),
    "value_propositions": ("id", "statement"),
    "target_customers": ("id", "segment"),
    "conversion_actions": ("id", "label"),
    "brand_terms": ("term", "term"),
    "niche_keywords": ("id", "term"),
    "competitor_hints": ("name", "name"),
    "content_pillars": ("id", "name"),
}
IDENTITY_SCALARS = (("name", "name_meta"), ("one_liner", "one_liner_meta"), ("industry", "industry_meta"))


@dataclass(frozen=True)
class Tombstone:
    collection: str
    item_key: str
    text: str


@dataclass(frozen=True)
class EditOp:
    op: Literal["confirm", "edit", "reject", "add"]
    collection: str
    item_id: str | None = None
    fields: dict[str, Any] | None = None
    item: dict[str, Any] | None = None


def item_key(item: BaseModel, collection: str) -> str:
    attribute = MERGEABLE[collection][0]
    value = str(getattr(item, attribute))
    return normalise_name(value) if attribute in {"term", "name"} else value


def item_text(item: BaseModel, collection: str) -> str:
    return str(getattr(item, MERGEABLE[collection][1]))


def _locked(item: BaseModel) -> bool:
    meta = getattr(item, "meta", None)
    return isinstance(meta, ClaimMeta) and meta.locked


def _with_meta(item: BaseModel, **changes: Any) -> BaseModel:
    meta: ClaimMeta = item.meta  # type: ignore[attr-defined]
    return item.model_copy(update={"meta": meta.model_copy(update=changes)})


def tombstoned(collection: str, item: BaseModel, tombstones: Sequence[Tombstone]) -> bool:
    key, text = item_key(item, collection), normalise_name(item_text(item, collection))
    for stone in tombstones:
        if stone.collection != collection:
            continue
        if stone.item_key == key or fuzz.token_set_ratio(text, normalise_name(stone.text)) >= TOMBSTONE_MATCH:
            return True
    return False


class ProfileMerger:
    """re-crawl merge: locked items win, unlocked items follow the new crawl, rejected items stay out (§5.14)."""

    def merge(
        self, current: BusinessProfile | None, incoming: BusinessProfile, tombstones: Sequence[Tombstone]
    ) -> BusinessProfile:
        update: dict[str, Any] = {}
        for collection in MERGEABLE:
            theirs = [
                item for item in getattr(incoming, collection) if not tombstoned(collection, item, tombstones)
            ]
            if current is None:
                update[collection] = theirs
                continue
            theirs_by_key = {item_key(item, collection): item for item in theirs}
            merged: list[BaseModel] = []
            seen: set[str] = set()
            for item in getattr(current, collection):
                key = item_key(item, collection)
                if not _locked(item):
                    continue
                seen.add(key)
                # a user-confirmed item the crawl no longer finds stays, marked stale for review
                merged.append(item if key in theirs_by_key else _with_meta(item, status=ClaimStatus.stale))
            merged.extend(item for key, item in theirs_by_key.items() if key not in seen)
            update[collection] = merged
        identity = incoming.identity
        if current is not None:
            changes: dict[str, Any] = {}
            for field, meta_field in IDENTITY_SCALARS:
                ours_meta: ClaimMeta = getattr(current.identity, meta_field)
                if ours_meta.locked:
                    changes[field] = getattr(current.identity, field)
                    changes[meta_field] = ours_meta
            identity = identity.model_copy(update=changes)
        return incoming.model_copy(update={**update, "identity": identity})


class ProfileEditor:
    """applies user ops addressed by stable item id; confirmed and edited items get locked."""

    def apply(
        self, profile: BusinessProfile, ops: Sequence[EditOp], *, now: datetime
    ) -> tuple[BusinessProfile, list[Tombstone]]:
        update: dict[str, list[BaseModel]] = {}
        stones: list[Tombstone] = []
        for op in ops:
            if op.collection not in MERGEABLE:
                raise ValueError(f"collection '{op.collection}' cannot be edited")
            items = list(update.get(op.collection, getattr(profile, op.collection)))
            if op.op == "add":
                if op.item is None:
                    raise ValueError("an add op needs an item")
                model = type(items[0]) if items else _item_model(op.collection)
                meta = ClaimMeta(
                    origin=ClaimOrigin.user,
                    status=ClaimStatus.confirmed,
                    confidence=1.0,
                    locked=True,
                    first_seen_version=profile.version + 1,
                    updated_at=now,
                )
                items.append(model.model_validate({**op.item, "meta": meta}))
            else:
                index = next(
                    (
                        i
                        for i, item in enumerate(items)
                        if str(getattr(item, MERGEABLE[op.collection][0])) == op.item_id
                    ),
                    None,
                )
                if index is None:
                    raise KeyError(f"{op.collection}/{op.item_id} not found")
                item = items[index]
                if op.op == "reject":
                    stones.append(
                        Tombstone(
                            op.collection, item_key(item, op.collection), item_text(item, op.collection)
                        )
                    )
                    items.pop(index)
                elif op.op == "confirm":
                    items[index] = _with_meta(item, status=ClaimStatus.confirmed, locked=True, updated_at=now)
                else:
                    edited = item.model_copy(update=op.fields or {})
                    items[index] = _with_meta(edited, status=ClaimStatus.edited, locked=True, updated_at=now)
            update[op.collection] = items
        return profile.model_copy(
            update={**update, "version": profile.version + 1, "parent_version": profile.version}
        ), stones


def _item_model(collection: str) -> type[BaseModel]:
    annotation = BusinessProfile.model_fields[collection].annotation
    return annotation.__args__[0]  # type: ignore[union-attr]
