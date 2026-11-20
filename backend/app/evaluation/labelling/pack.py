import html
import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from app.evaluation.labelling.guidelines import LABELS, REASON_CODES, guidelines_html
from app.evaluation.labelling.models import Population, SampleDesign, SampledTask, UploadPoint
from app.evaluation.labelling.sampler import size_band
from app.evaluation.packs.renderer import PackRenderer
from app.evaluation.packs.schema import FormField, PackItem, PackSchema

SIZE_LABELS = {"S0": "hidden", "S1": "under 10k", "S2": "10k-100k", "S3": "100k-1M", "S4": "over 1M"}
HIDDEN = frozenset(
    {"detector_ratio", "detector_label", "ratio", "is_outlier", "baseline", "stratum", "inclusion_prob"}
)
ANCHOR_NOTE = (
    "<p><b>Before you start:</b> label the 10 anchor videos sent separately and join the 15-minute "
    "calibration call. Then label this pack.</p>"
)

LABEL_SCHEMA = PackSchema(
    kind="outlier_labels",
    title="GrowthHacker outlier labelling pack",
    id_column="task_id",
    fields=(
        FormField("label", "Your answer", "choice", LABELS),
        FormField("reason_codes", "Reasons (optional)", "multi", REASON_CODES, required=False),
        FormField("note", "Note (optional)", "text", required=False),
    ),
    hidden_fields=HIDDEN,
    header_html=guidelines_html(),
)


def pack_expiry(design: SampleDesign, retention_days: int) -> datetime:
    """the pack holds api data, so it expires with its oldest datum (developer policies iii.e.4)."""
    return min(task.video.oldest_api_at for task in design.tasks) + timedelta(days=retention_days)


def _chart(points: Sequence[UploadPoint], target_id: str) -> str:
    if not points:
        return ""
    width, height, pad = 560, 170, 28
    times = [p.published_at.timestamp() for p in points]
    logs = [math.log10(p.views + 1) for p in points]
    t0, t1 = min(times), max(times)
    v0, v1 = min(logs), max(logs)
    span_t, span_v = (t1 - t0) or 1.0, (v1 - v0) or 1.0

    def xy(t: float, v: float) -> tuple[float, float]:
        return (
            pad + (t - t0) / span_t * (width - 2 * pad),
            height - pad - (v - v0) / span_v * (height - 2 * pad),
        )

    dots = []
    for point, t, v in zip(points, times, logs, strict=True):
        x, y = xy(t, v)
        target = point.video_id == target_id
        colour, radius = ("#d33", 6) if target else ("#4a6", 3.5)
        label = html.escape(f"{point.views:,} views at {point.age_days:.0f} days")
        dots.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{colour}"><title>{label}</title></circle>'
        )
    return (
        f'<svg width="{width}" height="{height}" role="img" aria-label="views by publish date, log scale">'
        f'<text x="2" y="12" font-size="11">views (log scale)</text>'
        f'<text x="{width - 140}" y="{height - 4}" font-size="11">publish date →</text>'
        f"{''.join(dots)}</svg>"
    )


def _uploads_table(points: Sequence[UploadPoint], target_id: str) -> str:
    rows = "".join(
        f'<tr class="{"target" if p.video_id == target_id else ""}"><td>{p.published_at.date()}</td>'
        f"<td>{p.age_days:.0f}</td><td>{html.escape(p.segment.replace('_', ' '))}</td>"
        f"<td>{p.views:,}</td><td>{html.escape(p.title[:90])}</td></tr>"
        for p in points
    )
    return (
        "<details><summary>Channel's last uploads</summary><table><tr><th>published</th><th>age (days)</th>"
        f"<th>format</th><th>views</th><th>title</th></tr>{rows}</table></details>"
    )


def label_item(task: SampledTask, population: Population) -> PackItem:
    video = task.video
    uploads = population.uploads.get(video.channel_id, ())
    if all(p.video_id != video.video_id for p in uploads):
        uploads = (
            UploadPoint(
                video_id=video.video_id,
                title=video.title,
                published_at=video.published_at,
                views=video.views,
                age_days=video.age_days,
                segment=video.segment,
            ),
            *uploads,
        )
    visible = {
        "url": f"https://www.youtube.com/watch?v={video.video_id}",
        "title": video.title,
        "channel_title": video.channel_title,
        "segment": video.segment,
        "duration_s": video.duration_s,
        "published_at": video.published_at.isoformat(),
        "age_days": video.age_days,
        "views": video.views,
        "observed_at": population.observed_at.isoformat(),
        "subscriber_band": SIZE_LABELS[size_band(video)],
        "thumbnail_url": video.thumbnail_url,
    }
    thumbnail = (
        f'<img src="{html.escape(video.thumbnail_url)}" alt="" width="240" loading="lazy" '
        'referrerpolicy="no-referrer">'
        if video.thumbnail_url
        else ""
    )
    duration = (
        "unknown" if video.duration_s is None else f"{video.duration_s // 60}m {video.duration_s % 60}s"
    )
    body = (
        f'{thumbnail}<p><a href="{html.escape(visible["url"])}" target="_blank" rel="noopener">'
        f"{html.escape(video.title)}</a><br>{html.escape(video.channel_title)} · subscribers "
        f"{html.escape(visible['subscriber_band'])}</p>"
        f"<p>Format: <b>{html.escape(video.segment.replace('_', ' '))}</b> · duration {duration} · published "
        f"{video.published_at.date()} · age at observation <b>{video.age_days:.1f} days</b> · views at "
        f"observation <b>{video.views:,}</b> (observed {population.observed_at:%Y-%m-%d %H:%M} UTC)</p>"
        f"{_chart(sorted(uploads, key=lambda p: p.published_at), video.video_id)}"
        f"{_uploads_table(uploads, video.video_id)}"
    )
    return PackItem(item_id=str(task.task_id), visible=visible, body_html=body)


def render_label_pack(
    design: SampleDesign,
    population: Population,
    *,
    participant: str,
    seed: int,
    batch_size: int,
    retention_days: int,
    now: datetime,
) -> str:
    renderer = PackRenderer(LABEL_SCHEMA, batch_size)
    return renderer.render(
        [label_item(task, population) for task in design.tasks],
        pack_id=str(design.batch_id),
        participant=participant,
        seed=seed,
        generated_at=now,
        expires_at=pack_expiry(design, retention_days),
        preface_html=ANCHOR_NOTE,
    )
