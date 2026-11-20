import re

from app.domain.card_copy import CardCopy
from app.domain.recommendations import RecommendationDraft
from app.services.recommend.gates.base import ALL_TYPES, Gate, GateEnv, GateOutcome, Phase, register_gate
from app.services.recommend.text.numbers import AllowedNumbers, unsupported_numbers

URL = re.compile(r"https?://|www\.|youtube\.com|youtu\.be", re.IGNORECASE)
RANGE = re.compile(r"\d+(?:\.\d+)?\s*[x×]?\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*[x×]", re.IGNORECASE)
FRACTION_PARAMS = (
    ("k", "n"),
    ("covering", "total"),
    ("n_outliers_with", "n_outliers_total"),
    ("n_with", "n_total"),
)


def card_texts(written: CardCopy) -> list[str]:
    texts = [written.summary, *written.why_it_works, written.fit_to_client]
    if written.working_title:
        texts.append(written.working_title)
    return texts


def allowed_numbers(draft: RecommendationDraft, env: GateEnv) -> AllowedNumbers:
    """every numeric leaf of the evidence brief, plus the dates the card may mention."""
    allowed = AllowedNumbers()
    allowed.add(draft.window_days)
    allowed.add(draft.as_of.year)
    allowed.add(len(draft.references))
    for reason in [*draft.reasons, *draft.fit_reasons, *(r for lens in draft.lenses for r in lens.reasons)]:
        for value in reason.params.values():
            if isinstance(value, str):
                allowed.add_text(value)
            else:
                allowed.add(value)
        for number in reason.numbers:
            allowed.add(number.value)
        for part, whole in FRACTION_PARAMS:
            allowed.add_fraction(reason.params.get(part), reason.params.get(whole))
    for ref in draft.references:
        allowed.add(ref.views_at_age.views)
        allowed.add(ref.views_at_age.age_days)
        allowed.add(ref.outlier_ratio)
        allowed.add(ref.channel_baseline_views_at_age)
        allowed.dates.add(ref.published_at.date())
        if ref.length_band:
            allowed.add_text(ref.length_band)
    if draft.confidence is not None:
        allowed.add(draft.confidence.outliers)
        allowed.add(draft.confidence.channels)
        allowed.add(draft.confidence.matching_videos)
        allowed.add_fraction(draft.confidence.outliers, draft.confidence.matching_videos)
    if draft.expected_range is not None:
        allowed.add(draft.expected_range.low)
        allowed.add(draft.expected_range.high)
        allowed.add(draft.expected_range.comparison_age_days)
        if draft.expected_range.views_at_age_range:
            for value in draft.expected_range.views_at_age_range:
                allowed.add(value)
    subject = draft.subject.model_dump(mode="json")
    for value in _leaves(subject):
        if isinstance(value, str):
            allowed.add_text(value)
        elif isinstance(value, int | float):
            allowed.add(value)
    if draft.gap is not None:
        for value in (
            draft.gap.competitors_covering,
            draft.gap.competitors_total,
            draft.gap.client_videos_matching,
        ):
            allowed.add(value)
        allowed.add_fraction(draft.gap.competitors_covering, draft.gap.competitors_total)
        if draft.gap.client_last_covered_at:
            allowed.dates.add(draft.gap.client_last_covered_at)
    allowed.dates.update(draft.allowed_dates)
    allowed.dates.add(draft.as_of.date())
    for location in env.ctx.client.locations:
        allowed.add_text(location)
    return allowed


def _leaves(value: object) -> list[object]:
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    return [value]


@register_gate
class ReasonIntegrity(Gate):
    """each written reason maps to a structured reason; no extra bullets, no video ids or links in text."""

    name = "reason_integrity"
    phase = Phase.post_writer
    applies_to = ALL_TYPES
    order = 110

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        assert written is not None
        reason_ids = {reason.reason_id for reason in draft.reasons}
        fit_ids = {reason.reason_id for reason in draft.fit_reasons}
        if len(written.reason_fact_ids) != len(written.why_it_works) or len(written.why_it_works) > len(
            reason_ids
        ):
            return self.suppressed(draft, "reason_count_mismatch")
        for ids in written.reason_fact_ids:
            if not ids or not set(ids) <= reason_ids:
                return self.suppressed(draft, "unknown_reason", ids=",".join(ids))
        if not written.fit_fact_ids or not set(written.fit_fact_ids) <= fit_ids:
            return self.suppressed(draft, "unknown_fit_reason", ids=",".join(written.fit_fact_ids))
        identifiers = {ref.video_id for ref in draft.reference_pool} | {
            ref.channel_id for ref in draft.reference_pool
        }
        for text in card_texts(written):
            if URL.search(text) or any(identifier in text for identifier in identifiers):
                return self.suppressed(draft, "identifier_in_text", text=text[:80])
        return self.passed(draft)


@register_gate
class NumbersFromEvidence(Gate):
    name = "numbers_from_evidence"
    phase = Phase.post_writer
    applies_to = ALL_TYPES
    order = 120

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        assert written is not None
        offending = unsupported_numbers(card_texts(written), allowed_numbers(draft, env))
        if offending:
            return self.suppressed(draft, "number_not_in_evidence", numbers=", ".join(offending))
        return self.passed(draft)


@register_gate
class NoCopyingText(Gate):
    name = "no_copying_text"
    phase = Phase.post_writer
    applies_to = ALL_TYPES
    order = 130

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        assert written is not None
        for text in (written.summary, written.working_title):
            hit = env.copy.check(text) if text else None
            if hit is not None:
                return self.suppressed(
                    draft, "copy_risk", rule=hit.rule, score=round(hit.score, 1), title=hit.title
                )
        return self.passed(draft)


@register_gate
class PolicyGuardrailsText(Gate):
    name = "policy_guardrails_text"
    phase = Phase.post_writer
    applies_to = ALL_TYPES
    order = 140

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        assert written is not None
        texts = card_texts(written)
        hits = env.policy.find(texts) + (env.domain_policy.find(texts) if env.domain_policy else [])
        if hits:
            return self.suppressed(draft, "policy_text", pattern=hits[0].pattern_id, text=hits[0].text)
        return self.passed(draft)


@register_gate
class CertaintyLanguage(Gate):
    name = "certainty_language"
    phase = Phase.post_writer
    applies_to = ALL_TYPES
    order = 150

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        assert written is not None
        texts = card_texts(written)
        hits = env.certainty.find(texts)
        if hits:
            return self.suppressed(draft, "certainty_claim", pattern=hits[0].pattern_id, text=hits[0].text)
        for text in texts:
            if RANGE.search(text) and "indicative" not in text.lower():
                return self.suppressed(draft, "range_without_indicative", text=text[:80])
        return self.passed(draft)
