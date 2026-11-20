from dataclasses import dataclass, field

from app.domain.recommendations import (
    ASSIGNMENT_REC_TYPES,
    InsufficiencyCounts,
    InsufficiencyNotice,
    InsufficiencySuggestion,
    RecType,
)
from app.services.recommend.context import RecommendContext

MIN_COMPETITORS = 3


@dataclass(frozen=True, slots=True)
class WindowSummary:
    window_days: int
    outliers: int
    channels: int
    videos: int
    cards: int
    type_cards: dict[RecType, int] = field(default_factory=dict)

    def counts(self) -> InsufficiencyCounts:
        return InsufficiencyCounts(
            outliers=self.outliers, channels=self.channels, videos=self.videos, cards=self.cards
        )


class ThinDataAdvisor:
    """says so when data is thin, with projected counts for wider windows computed from cached data."""

    def notices(
        self, ctx: RecommendContext, summary: WindowSummary, wider: list[WindowSummary], matched_topics: int
    ) -> list[InsufficiencyNotice]:
        settings = ctx.settings
        required = InsufficiencyCounts(
            outliers=settings.min_window_outliers,
            channels=settings.min_outlier_channels,
            cards=settings.min_cards,
        )
        notices: list[InsufficiencyNotice] = []
        thin = self._thin(ctx, summary)
        if thin:
            notices.append(
                InsufficiencyNotice(
                    kind="thin_window",
                    window_days=summary.window_days,
                    found=summary.counts(),
                    required=required,
                    suggestion=self._suggestion(ctx, wider, matched_topics),
                    reason_code="THIN_WINDOW",
                )
            )
        else:
            notices.extend(self._type_thin(ctx, summary, wider))
        notices.extend(self._context_notices(ctx, summary))
        return notices

    def _thin(self, ctx: RecommendContext, summary: WindowSummary) -> bool:
        settings = ctx.settings
        return (
            summary.cards < settings.min_cards
            or summary.outliers < settings.min_window_outliers
            or summary.channels < settings.min_outlier_channels
        )

    def _suggestion(
        self, ctx: RecommendContext, wider: list[WindowSummary], matched_topics: int
    ) -> InsufficiencySuggestion:
        for candidate in wider:
            if not self._thin(ctx, candidate):
                return InsufficiencySuggestion(
                    action="widen_window", widen_to=candidate.window_days, projected=candidate.counts()
                )
        if len(ctx.competitors) < MIN_COMPETITORS:
            return InsufficiencySuggestion(action="add_competitors")
        if matched_topics < ctx.settings.min_matched_topics:
            return InsufficiencySuggestion(action="add_keywords")
        return InsufficiencySuggestion(action="wait")

    def _type_thin(
        self, ctx: RecommendContext, summary: WindowSummary, wider: list[WindowSummary]
    ) -> list[InsufficiencyNotice]:
        notices = []
        for rec_type in sorted(ASSIGNMENT_REC_TYPES, key=lambda item: item.value):
            if summary.type_cards.get(rec_type, 0) > 0:
                continue
            target = next((item for item in wider if item.type_cards.get(rec_type, 0) > 0), None)
            if target is None:
                continue
            notices.append(
                InsufficiencyNotice(
                    kind="type_thin",
                    window_days=summary.window_days,
                    rec_type=rec_type,
                    found=InsufficiencyCounts(cards=0),
                    required=InsufficiencyCounts(cards=1),
                    suggestion=InsufficiencySuggestion(
                        action="widen_window",
                        widen_to=target.window_days,
                        projected=InsufficiencyCounts(cards=target.type_cards[rec_type]),
                    ),
                    reason_code="TYPE_THIN",
                )
            )
        return notices

    def _context_notices(self, ctx: RecommendContext, summary: WindowSummary) -> list[InsufficiencyNotice]:
        notices = []
        if not ctx.catalog.buyer_questions():
            notices.append(
                self._notice(summary, "no_buyer_questions", "add_buyer_questions", "NO_BUYER_QUESTIONS")
            )
        if ctx.index.client_baseline() is None:
            notices.append(self._notice(summary, "no_client_baseline", "wait", "NO_CLIENT_BASELINE"))
        if len(ctx.competitors) < MIN_COMPETITORS:
            notice = self._notice(summary, "few_competitors", "add_competitors", "FEW_COMPETITORS")
            notices.append(
                notice.model_copy(
                    update={
                        "found": InsufficiencyCounts(channels=len(ctx.competitors)),
                        "required": InsufficiencyCounts(channels=MIN_COMPETITORS),
                    }
                )
            )
        if ctx.stale_data:
            notices.append(self._notice(summary, "stale_data", "wait", "STALE_DATA"))
        return notices

    @staticmethod
    def _notice(summary: WindowSummary, kind: str, action: str, code: str) -> InsufficiencyNotice:
        return InsufficiencyNotice(
            kind=kind,
            window_days=summary.window_days,
            found=summary.counts(),
            required=InsufficiencyCounts(),
            suggestion=InsufficiencySuggestion(action=action),
            reason_code=code,
        )


def render_notice(notice: InsufficiencyNotice) -> str:
    # notices are always rendered by template, never by the writer
    found, suggestion = notice.found, notice.suggestion
    window = notice.window_days
    if notice.kind == "thin_window":
        text = (
            f"Not enough outliers in the last {window} days ({found.outliers} across {found.channels} "
            f"{'channel' if found.channels == 1 else 'channels'})."
        )
        if suggestion.action == "widen_window" and suggestion.projected is not None:
            projected = suggestion.projected
            return (
                f"{text} Widen to {suggestion.widen_to}? That gives {projected.outliers} outliers across "
                f"{projected.channels} channels."
            )
        if suggestion.action == "add_competitors":
            return f"{text} Adding more competitors would give us more to learn from."
        if suggestion.action == "add_keywords":
            return f"{text} Adding narrower keywords would help us find matching videos."
        return f"{text} We'll check again as new videos come in."
    if notice.kind == "type_thin" and notice.rec_type is not None:
        label = notice.rec_type.value.replace("_", " ")
        return f"No {label} suggestions for {window} days. Widen to {suggestion.widen_to}?"
    if notice.kind == "no_buyer_questions":
        return "Add buyer questions to get conversion-focused ideas."
    if notice.kind == "no_client_baseline":
        return "No baseline yet: ranges are relative to similar channels' own normal (indicative)."
    if notice.kind == "few_competitors":
        return f"Only {found.channels} competitors confirmed; most evidence comes from the wider niche."
    return "Data last updated more than 48 hours ago."
