from collections.abc import Sequence

from app.core.config import RefreshSettings
from app.domain.recommendations import ExpectedRange
from app.services.refresh.inputs import PastImpact
from app.services.refresh.triggers.base import Finding, TriggerKind

MEASURED_VERDICTS = frozenset({"up", "down", "no_clear_change"})
NOT_ESTIMATED = "Not estimated (no comparable refresh history)"
HYGIENE_EFFECTS = {
    TriggerKind.broken_link: "Fixes a link viewers can't use.",
    TriggerKind.missing_cta: "Gives viewers who want to book a direct link.",
    TriggerKind.missing_contact: "Gives viewers a direct way to call or message you.",
    TriggerKind.missing_hashtags: "Adds hashtags drawn from what the video already covers.",
}


def expected_effect(
    findings: Sequence[Finding],
    past: Sequence[PastImpact],
    settings: RefreshSettings,
    comparison_age_days: int,
) -> tuple[str, ExpectedRange]:
    """honest effect text: no number unless the client's own measured refreshes support one."""
    keyword = next(
        (finding for finding in findings if finding.keyword_trigger and finding.proposes_text), None
    )
    if keyword is None:
        lines = [HYGIENE_EFFECTS[finding.kind] for finding in findings if finding.kind in HYGIENE_EFFECTS]
        text = (
            " ".join(dict.fromkeys(lines)) or "Fixes the issues listed below; no change in views is claimed."
        )
        return text, ExpectedRange(
            low=None, high=None, basis="not_applicable", comparison_age_days=comparison_age_days, display=text
        )
    days = settings.impact_window_days
    text = (
        f"Adds '{keyword.keyword}' where search can match it. We can't predict a number: description edits "
        f"change relevance, not demand. We'll compare this video's daily views for {days} days before and after "
        "you apply it."
    )
    measured = [
        item.impact.adjusted_ratio
        for item in past
        if item.trigger_kind == keyword.kind.value
        and item.impact.verdict in MEASURED_VERDICTS
        and item.impact.adjusted_ratio is not None
    ]
    if len(measured) >= settings.min_own_history:
        low, high = min(measured), max(measured)
        display = (
            f"Your last {len(measured)} keyword refreshes changed daily views by {low:.1f}x-{high:.1f}x "
            "vs comparable videos (indicative)"
        )
        return text, ExpectedRange(
            low=round(low, 2),
            high=round(high, 2),
            basis="own",
            n_own=len(measured),
            comparison_age_days=comparison_age_days,
            display=display,
        )
    return text, ExpectedRange(
        low=None,
        high=None,
        basis="insufficient",
        comparison_age_days=comparison_age_days,
        display=NOT_ESTIMATED,
    )
