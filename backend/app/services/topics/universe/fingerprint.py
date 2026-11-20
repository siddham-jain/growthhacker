import hashlib
import json
from collections.abc import Mapping, Sequence
from uuid import UUID

from app.domain.topics import TopicKind, TopicRef


def topic_ref_id(client_id: UUID, kind: TopicKind, canonical_key: str) -> str:
    # stable across profile versions while the client's wording is unchanged
    return "tr_" + hashlib.sha1(f"{client_id}|{kind.value}|{canonical_key}".encode()).hexdigest()[:12]


def ref_fingerprint(ref: TopicRef) -> str:
    payload = {
        "label": ref.label,
        "definition": ref.definition,
        "aliases": sorted(
            [alias.norm_key, alias.kind, alias.status, alias.weight, alias.ambiguous, alias.match_mode]
            for alias in ref.aliases
        ),
        "negatives": sorted(ref.negative_contexts),
        "facets": sorted(facet.value for facet in ref.facets),
        "parents": sorted(ref.parent_ids),
        "core": ref.core_entity_terms,
        "locality": ref.locality_terms,
        "status": ref.status,
        "weight": ref.weight,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def universe_hash(
    refs: Sequence[TopicRef], profile_version: int, engine_version: str, embedder_model_ids: Mapping[str, str]
) -> str:
    # the profile version is part of the hash because topic_universes keeps one row per version
    payload = json.dumps(
        {
            "refs": sorted(ref.id + ref.fingerprint for ref in refs),
            "profile_version": profile_version,
            "engine": engine_version,
            "embedders": dict(sorted(embedder_model_ids.items())),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def ref_text(ref: TopicRef) -> str:
    """the text a ref is embedded with: label, definition and up to five confirmed aliases."""
    confirmed = [
        alias.text for alias in ref.aliases if alias.status == "confirmed" and alias.kind != "canonical"
    ]
    return "; ".join(part for part in [ref.label, ref.definition, *confirmed[:5]] if part)
