from app.domain.topics import MatchEvidence, MatchMethod, TopicKind, TopicMatch, TopicRef

KIND_LABEL = {
    TopicKind.keyword: "keyword",
    TopicKind.offering: "offering",
    TopicKind.buyer_question: "question",
    TopicKind.news_entity: "news topic",
}
FIELD_LABEL = {
    "title": "the title",
    "chapters": "a chapter title",
    "description_head": "the start of the description",
    "description_rest": "the description",
    "tags": "the tags",
    "hashtags": "the hashtags",
    "combined": "the title and description",
}
ALIAS_LABEL = {
    "canonical": "your term",
    "client_alias": "your alias",
    "abbreviation": "abbreviation from your site",
    "transliteration": "transliteration",
    "translation": "translation",
    "synonym": "synonym",
    "spelling_variant": "spelling variant",
    "morph_variant": "word form",
}


def _lexical_phrase(evidence: MatchEvidence) -> str:
    alias = ALIAS_LABEL.get(evidence.alias_kind or "", "alias")
    if evidence.alias_status == "proposed":
        alias += ", suggested and not yet confirmed"
    phrase = f"{FIELD_LABEL.get(evidence.field or '', 'the text')} contains '{evidence.span_text}' ({alias})"
    if evidence.negated:
        phrase += " in a negative context"
    if evidence.subsumed_by_ref:
        phrase += " inside a longer topic"
    return phrase


class MatchExplainer:
    """one deterministic sentence per match, rendered from its evidence and method."""

    def explain(
        self,
        match: TopicMatch,
        ref: TopicRef,
        *,
        implied_label: str | None = None,
        footer_removed: bool = False,
    ) -> str:
        subject = f"your {KIND_LABEL[ref.kind]} '{ref.label}'"
        reasons = []
        lexical = [item for item in match.evidence if item.method == "lexical"]
        semantic = next((item for item in match.evidence if item.method == "semantic"), None)
        llm = next((item for item in match.evidence if item.method == "llm"), None)
        if match.method is MatchMethod.override:
            reasons.append("set by a reviewer")
        if match.method is MatchMethod.implied and implied_label:
            reasons.append(f"implied by the matched question '{implied_label}'")
        reasons += [_lexical_phrase(item) for item in lexical[:2]]
        if semantic is not None and (match.semantic_score or 0) > 0:
            reasons.append(
                f"close in meaning to your wording (margin {semantic.detail.get('margin', 0):+.2f})"
            )
        if llm is not None:
            reasons.append(f"borderline, confirmed by a constrained check quoting '{llm.span_text}'")
        if lexical and all(item.field in {"tags", "hashtags"} for item in lexical):
            reasons.append("matched from tags only")
        if footer_removed:
            reasons.append("channel footer ignored")
        if match.decision == "accepted":
            verdict = f"Matched to {subject}"
        elif match.decision == "unadjudicated":
            verdict = f"Pending check for {subject}"
        else:
            verdict = f"Not counted for {subject}"
        detail = "; ".join(reasons) if reasons else "no supporting text"
        return (
            f"{verdict}: {detail} ({match.confidence} confidence, based on title, description and tags only)."
        )
