import html
from collections.abc import Sequence
from datetime import datetime

from app.evaluation.packs.renderer import PackRenderer
from app.evaluation.packs.schema import FormField, PackItem, PackSchema
from app.evaluation.review.models import ClientSheet, ReviewCard, ReviewSampleDesign
from app.evaluation.review.rubric import RUBRIC, rubric_html
from app.models.evaluation import REVIEW_CRITERIA

HIDDEN = frozenset(
    {"recommendation_id", "rec_id", "db_id", "is_decoy", "arm", "gate_features", "model", "stable_key"}
)
TYPE_LABELS = {
    "video_idea": "Video idea",
    "title_pattern": "Title pattern",
    "thumbnail_pattern": "Thumbnail pattern",
    "format": "Format",
    "creator": "Creator",
    "news_opportunity": "News opportunity",
    "description_refresh": "Description refresh",
}

REVIEW_SCHEMA = PackSchema(
    kind="card_review",
    title="GrowthHacker recommendation review pack",
    id_column="item_id",
    fields=(
        *(FormField(criterion, RUBRIC[criterion][0], "scale") for criterion in REVIEW_CRITERIA),
        FormField("would_ship", "Would you ship this card?", "bool"),
        FormField("comment", "Comment (optional)", "text", required=False),
    ),
    hidden_fields=HIDDEN,
    header_html=rubric_html(),
)


def _list(items: Sequence[str]) -> str:
    return (
        "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"
        if items
        else "<p>none</p>"
    )


def sheet_html(sheet: ClientSheet) -> str:
    facts = [f"window: last {sheet.window_days} days"]
    if sheet.website:
        link = html.escape(sheet.website)
        facts.insert(0, f'<a href="{link}" target="_blank" rel="noopener">{link}</a>')
    return (
        f'<section class="sheet"><h2>Client: {html.escape(sheet.name)}</h2><p>{" · ".join(facts)}</p>'
        f"<p>{html.escape(sheet.summary or 'No business summary.')}</p>"
        f"<h3>Offerings</h3>{_list(sheet.offerings)}<h3>Tracked buyer questions</h3>{_list(sheet.buyer_questions)}"
        f"<h3>Competitors</h3>{_list(sheet.competitors)}</section>"
    )


def card_html(card: ReviewCard) -> str:
    references = "".join(
        "<li>"
        + (
            f'<img src="{html.escape(ref.thumbnail_url)}" alt="" width="120" loading="lazy" '
            'referrerpolicy="no-referrer"> '
            if ref.thumbnail_url
            else ""
        )
        + f'<a href="{html.escape(ref.url)}" target="_blank" rel="noopener">{html.escape(ref.title)}</a> · '
        f"{html.escape(ref.channel)} · {ref.published_on} · {html.escape(ref.views_label)}"
        + (f" · {ref.outlier_ratio:.1f}x its channel's normal" if ref.outlier_ratio is not None else "")
        + "</li>"
        for ref in card.reference_videos
    )
    question = (
        f"<p><b>Buyer question:</b> {html.escape(card.buyer_question)}</p>" if card.buyer_question else ""
    )
    expected = html.escape(card.expected_range) if card.expected_range else "not estimated"
    return (
        f"<p><i>{html.escape(card.client_name)} · {html.escape(TYPE_LABELS.get(card.type, card.type))} · lens "
        f"{html.escape(', '.join(card.lens))}</i></p><h3>{html.escape(card.summary)}</h3>{question}"
        f"<p><b>Why it works</b></p>{_list(card.why_it_works)}<p><b>Reference videos</b></p><ul>{references}</ul>"
        f"<p><b>Fit to client:</b> {html.escape(card.fit_to_client)}</p><p><b>Expected range:</b> {expected} · "
        f"<b>Confidence:</b> {html.escape(card.confidence)}</p>"
    )


def render_review_pack(
    design: ReviewSampleDesign, *, participant: str, seed: int, batch_size: int, now: datetime
) -> str:
    items = [
        PackItem(
            item_id=str(item.item_id),
            visible=item.source.card.model_dump(mode="json"),
            body_html=card_html(item.source.card),
        )
        for item in design.items
    ]
    return PackRenderer(REVIEW_SCHEMA, batch_size).render(
        items,
        pack_id=str(design.batch_id),
        participant=participant,
        seed=seed,
        generated_at=now,
        expires_at=min(item.expires_at for item in design.items),
        preface_html="".join(sheet_html(sheet) for sheet in design.sheets),
    )
