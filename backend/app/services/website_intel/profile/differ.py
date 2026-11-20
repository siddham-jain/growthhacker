from app.domain.profile import BusinessProfile, ProfileChange, ProfileDiff
from app.services.website_intel.profile.merger import MERGEABLE, item_key, item_text

HIGH_COLLECTIONS = frozenset({"conversion_actions"})
MEDIUM_COLLECTIONS = frozenset({"niche_keywords", "brand_terms"})
DRASTIC_OVERLAP = 0.3


def offering_overlap(old: BusinessProfile, new: BusinessProfile) -> float:
    before = {item_key(item, "offerings") for item in old.offerings}
    after = {item_key(item, "offerings") for item in new.offerings}
    if not before:
        return 1.0
    return len(before & after) / len(before)


class ProfileDiffer:
    """item-level diff with significance driving profile.changed (plan 03 §5.14)."""

    def diff(self, old: BusinessProfile | None, new: BusinessProfile) -> ProfileDiff:
        if old is None:
            return ProfileDiff(from_version=0, to_version=new.version, significance="high")
        added: list[str] = []
        removed: list[str] = []
        changed: list[ProfileChange] = []
        touched: set[str] = set()
        for collection in MERGEABLE:
            before = {item_key(item, collection): item for item in getattr(old, collection)}
            after = {item_key(item, collection): item for item in getattr(new, collection)}
            for key in after.keys() - before.keys():
                added.append(f"{collection}/{key}")
                touched.add(collection)
            for key in before.keys() - after.keys():
                removed.append(f"{collection}/{key}")
                touched.add(collection)
            for key in before.keys() & after.keys():
                old_text, new_text = item_text(before[key], collection), item_text(after[key], collection)
                if old_text != new_text:
                    changed.append(
                        ProfileChange(id=f"{collection}/{key}", field="text", before=old_text, after=new_text)
                    )
                    touched.add(collection)
        for field in ("name", "one_liner", "industry"):
            if getattr(old.identity, field) != getattr(new.identity, field):
                changed.append(
                    ProfileChange(
                        id=f"identity/{field}",
                        field=field,
                        before=str(getattr(old.identity, field)),
                        after=str(getattr(new.identity, field)),
                    )
                )
        old_primary = {item.id for item in old.offerings if item.is_primary}
        new_primary = {item.id for item in new.offerings if item.is_primary}
        high = (
            bool(touched & HIGH_COLLECTIONS)
            or old_primary != new_primary
            or old.freshness.sensitivity != new.freshness.sensitivity
            or set(old.buyer_question_ids) != set(new.buyer_question_ids)
        )
        if high:
            significance = "high"
        elif touched & MEDIUM_COLLECTIONS or old.locations.cities != new.locations.cities:
            significance = "medium"
        else:
            significance = "low"
        return ProfileDiff(
            from_version=old.version,
            to_version=new.version,
            added=added,
            removed=removed,
            changed=changed,
            significance=significance,  # type: ignore[arg-type]
        )
