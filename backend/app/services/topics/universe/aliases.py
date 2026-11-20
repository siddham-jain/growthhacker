from typing import Literal

from app.domain.topics import TopicAlias
from app.services.topics.text.normalize import clean_text
from app.services.topics.text.scripts import dominant_script, script_profile
from app.services.topics.text.tokenize import tokenize

AliasKind = Literal[
    "canonical",
    "client_alias",
    "morph_variant",
    "abbreviation",
    "transliteration",
    "translation",
    "synonym",
    "spelling_variant",
]
AliasOrigin = Literal["client", "profile_extracted", "rule", "llm_proposed"]
AliasStatus = Literal["confirmed", "proposed", "rejected"]


def norm_key(text: str) -> str:
    return " ".join(token.stem for token in tokenize(text))


def phonetic_text(text: str) -> str | None:
    keys = [token.phonetic for token in tokenize(text)]
    if not keys or any(key is None for key in keys):
        return None
    return " ".join(key for key in keys if key)


def make_alias(
    text: str,
    *,
    kind: AliasKind,
    origin: AliasOrigin,
    status: AliasStatus,
    anchor: str,
    weight: float,
    lang: str | None = None,
    ambiguous: bool = False,
) -> TopicAlias | None:
    cleaned = clean_text(text).replace("\n", " ")
    key = norm_key(cleaned)
    if not key:
        return None
    script = dominant_script(script_profile(cleaned)) or "Zyyy"
    return TopicAlias(
        text=cleaned,
        norm_key=key,
        phonetic_key=phonetic_text(cleaned),
        lang=lang,
        script=script,
        kind=kind,
        origin=origin,
        status=status,
        anchor_text=anchor,
        match_mode="token_exact_case" if kind == "abbreviation" else "phrase",
        weight=weight,
        ambiguous=ambiguous,
    )
