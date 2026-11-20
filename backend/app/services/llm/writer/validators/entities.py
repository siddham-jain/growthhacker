import re

from app.domain.card_copy import Violation
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.text_match import contains_phrase, normalize, partial_ratio
from app.services.llm.writer.validators.base import copy_fields, grounding_validators, masked_text

HANDLE = re.compile(r"(?<![\w@])@[\w.]{2,}")
DOMAIN = re.compile(
    r"https?://[^\s)]+|www\.[^\s)]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|org|net|io|co|ai|health|clinic|app)\b",
    re.I,
)
PROPER_NOUN = re.compile(r"\b[A-Z][\w'&-]*(?:\s+[A-Z][\w'&-]*)*")
FUZZY_MIN_CHARS = 6
FUZZY_THRESHOLD = 92
COMMON_CAPITALISED = frozenset(
    """
    i a an the this that these those your you we our it its make try use start add film post publish
    create show answer cover explain open lead pair shorts short youtube google instagram faq q&a monday
    tuesday wednesday thursday friday saturday sunday january february march april may june july august
    september october november december how why what when where which who
    """.split()  # noqa: SIM905
)


def _domain_of(raw: str) -> str:
    host = re.sub(r"^https?://", "", raw.lower()).split("/", 1)[0]
    return host.removeprefix("www.")


class EntityGrounding:
    """V3: no handle, domain, channel or brand that the evidence pack does not contain."""

    name = "entities"

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        grounding = built.grounding
        allowed_norm = {normalize(name) for name in grounding.allowed_entities}
        corpus = normalize(
            " ".join([*grounding.statements.values(), *grounding.allowed_entities, *grounding.masked_spans])
        )
        violations = []
        for field in copy_fields(copy):
            text = masked_text(field.text, built)
            for handle in HANDLE.findall(text):
                if normalize(handle) not in allowed_norm:
                    violations.append(
                        Violation(
                            rule="unknown_handle", field=field.name, token=handle, detail="remove the @handle"
                        )
                    )
            for raw in DOMAIN.findall(text):
                domain = _domain_of(raw)
                if not any(domain == own or domain.endswith(f".{own}") for own in grounding.client_domains):
                    violations.append(
                        Violation(
                            rule="unknown_domain", field=field.name, token=domain, detail="remove the link"
                        )
                    )
            for name in grounding.forbidden_entities:
                fuzzy = (
                    len(normalize(name)) >= FUZZY_MIN_CHARS and partial_ratio(name, text) >= FUZZY_THRESHOLD
                )
                if contains_phrase(text, name) or fuzzy:
                    violations.append(
                        Violation(
                            rule="unknown_channel_or_entity",
                            field=field.name,
                            detail="names a channel or brand that is not in the evidence; remove it",
                        )
                    )
                    break
            violations.extend(self._unverified(field.name, text, corpus))
        return violations

    def _unverified(self, field: str, text: str, corpus: str) -> list[Violation]:
        found = []
        for sentence in re.split(r"(?<=[.!?:])\s+", text):
            for index, match in enumerate(PROPER_NOUN.finditer(sentence)):
                span = match.group(0)
                words = normalize(span).split()
                if not words or all(word in COMMON_CAPITALISED for word in words):
                    continue
                if index == 0 and match.start() == 0 and len(words) == 1:
                    continue
                if f" {' '.join(words)} " in f" {corpus} ":
                    continue
                found.append(
                    Violation(
                        rule="unverified_entity", field=field, token=span, detail="name not found in the pack"
                    )
                )
        return found


grounding_validators.add("entities", lambda settings: EntityGrounding())
