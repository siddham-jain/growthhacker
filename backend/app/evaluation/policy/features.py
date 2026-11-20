"""the 50 computed features and their guardrail checks (plan 12 §9.1 with the 99 review applied).

the catalogue lives with 12 so one owner keeps it complete; each row names its owning component.
"""

from collections.abc import Iterable

from app.evaluation.policy.registry import GUARDRAILS, FeatureSpec, Maths, Scope, Source, feature_registry

API_DAYS = 28  # every owner acts at day 28 of the 30-day limit (99 C27)
LEDGER_DAYS = 400
NEWS_DAYS = 90
ALL: tuple[Scope, ...] = ("own", "competitor", "niche")
OWN: tuple[Scope, ...] = ("own",)
PEERS: tuple[Scope, ...] = ("competitor", "niche")
NONE: tuple[Scope, ...] = ()
YT: tuple[Source, ...] = ("yt_data_public",)
DISCLOSED = "labelled 'calculated by GrowthHacker, not a YouTube metric'"
SECTION_L = "storage past 30 days only after an accepted Section L derived-metrics audit"


def _fields(model: str, names: str) -> list[str]:
    return [f"{model}.{name}" for name in names.split()]


def feature(
    number: int,
    key: str,
    owner: str,
    name: str,
    sources: tuple[Source, ...],
    maths: tuple[Maths, ...],
    scope: tuple[Scope, ...],
    checks: str,
    condition: str,
    *,
    displayed: bool,
    stored: bool = True,
    retention_days: int | None = API_DAYS,
    covers: Iterable[str] = (),
    review_note: str | None = None,
) -> FeatureSpec:
    cells = checks.split()
    return feature_registry.register(
        FeatureSpec(
            number=number,
            key=key,
            owner=owner,
            name=name,
            sources=sources,
            maths=maths,
            scope=scope,
            displayed_as_metric=displayed,
            stored=stored,
            retention_days=retention_days if stored else None,
            checks=dict(zip(GUARDRAILS, cells, strict=True)),
            condition=condition,
            review_note=review_note,
            covers=frozenset(covers),
        )
    )


feature(
    1,
    "youtube.channel_stats",
    "02",
    "Channel stats: subscribers, views, video count; subscriber band",
    YT,
    ("raw", "threshold_flag"),
    ALL,
    "OK OK OK OK COND n/a",
    "refreshed by the daily ingest, deleted at 28 d when a channel is no longer tracked; size is shown as a "
    "subscriber band, never as a channel score",
    displayed=True,
    covers=_fields("ChannelScorecard", "subscriber_count subscriber_band"),
)
feature(
    2,
    "youtube.video_metadata",
    "02",
    "Video metadata: title, description, tags, duration, publishedAt, thumbnail URL, liveBroadcastContent",
    YT,
    ("raw",),
    ALL,
    "OK OK OK COND COND n/a",
    "`categoryId` and `topicDetails` are never used to classify (lint and tests); metadata refreshed or "
    "deleted by day 28",
    displayed=False,
)
feature(
    3,
    "youtube.video_stats",
    "02",
    "Video stats: views, likes, comments",
    YT,
    ("raw",),
    ALL,
    "OK OK OK OK COND n/a",
    "statistics are refreshed on batchGetStats or deleted at 28 d (III.E.4.b); " + SECTION_L,
    displayed=True,
    covers=_fields("VideoMetrics", "views_now likes_now comments_now"),
)
feature(
    4,
    "youtube.video_snapshots",
    "02",
    "VideoSnapshot time series on batchGetStats",
    YT,
    ("raw",),
    ALL,
    "OK OK OK OK FLAG n/a",
    "snapshots are collected on the batchGetStats bucket (99 C2) and dropped with their daily partition at 28 d",
    displayed=False,
    review_note="past view counts cannot be refreshed, so a same-age baseline may only use snapshots under "
    "28 days old; observed day-7 values therefore survive about 35 days after publish. Reviewer to confirm "
    "this reading of III.E.4.b, or the Section L audit is needed to keep them up to 36 months.",
)
feature(
    5,
    "youtube.comment_text",
    "02",
    "Comment text",
    YT,
    ("raw",),
    ALL,
    "OK OK OK COND COND n/a",
    "used transiently for comment signals; text deleted at 28 d, PII redacted, no author identity stored; "
    "excerpts off for healthcare and finance (D-POL-12)",
    displayed=False,
)
feature(
    6,
    "youtube.own_analytics",
    "02",
    "Own Analytics: watch time, retention, traffic sources, search terms, subscribers gained (OAuth)",
    ("yt_analytics_oauth",),
    ("raw",),
    OWN,
    "OK OK OK COND COND n/a",
    "ageGroup/gender/geography reports are never requested; authorised stats are shown only to the "
    "authorising client and kept with 30-day re-authorisation checks",
    displayed=True,
    retention_days=None,
)
feature(
    7,
    "youtube.quota_ledger",
    "02",
    "Quota ledger",
    ("internal",),
    ("count", "sum"),
    NONE,
    "n/a n/a n/a n/a n/a n/a",
    "internal telemetry; no API data stored",
    displayed=False,
    retention_days=LEDGER_DAYS,
)
feature(
    8,
    "analytics.video_age",
    "06",
    "Video age and metric provenance",
    YT,
    ("difference",),
    ALL,
    "OK OK OK OK COND n/a",
    "derived from publishedAt at the run's as_of; expires with its oldest input",
    displayed=True,
    covers=_fields("VideoMetrics", "video_id channel_id as_of age_anchor_at age_days metrics_version"),
)
feature(
    9,
    "analytics.views_at_age",
    "06",
    "ViewsAtAge observed or interpolated",
    YT,
    ("interpolation",),
    ALL,
    "COND OK OK OK COND n/a",
    "linear interpolation between two API observations only, labelled with its method; no curve fitting "
    "and never extrapolated",
    displayed=True,
    covers=_fields("VideoMetrics", "views_at_age comparison_age_days"),
)
feature(
    10,
    "analytics.views_at_age_proxy",
    "06",
    "ViewsAtAge age-band proxy for pre-tracking videos",
    YT,
    ("median", "ratio"),
    ALL,
    "COND OK OK OK COND n/a",
    "labelled 'proxy', never displayed as observed and never mixed with true values in one ratio",
    displayed=True,
    covers=_fields("VideoMetrics", "views_at_age"),
)
feature(
    11,
    "analytics.view_velocity",
    "06",
    "View velocity",
    YT,
    ("difference", "ratio"),
    ALL,
    "OK OK OK OK COND n/a",
    "views gained between two observations divided by the days between them; " + DISCLOSED,
    displayed=True,
    covers=_fields("VideoMetrics", "views_per_day_lifetime velocity_recent velocity_7d"),
)
feature(
    12,
    "analytics.engagement_rate",
    "06",
    "Engagement rate (likes + comments) / views",
    YT,
    ("sum", "ratio"),
    ALL,
    "OK OK OK OK COND n/a",
    f"simple ratio, {DISCLOSED}; {SECTION_L}",
    displayed=True,
    covers=_fields("VideoMetrics", "engagement_rate like_rate comment_rate engagement_basis"),
)
feature(
    13,
    "analytics.channel_baseline",
    "06",
    "ChannelBaseline: median and mean views at age by format",
    YT,
    ("median", "mean"),
    ALL,
    "OK OK OK OK COND n/a",
    "per channel and format; never pooled across owners or presented as one owner's data",
    displayed=True,
    covers=_fields("OwnPerformanceReport", "baselines"),
)
feature(
    14,
    "analytics.outlier_ratio",
    "06",
    "Outlier ratio and flag (views at age / own-channel median)",
    YT,
    ("ratio", "threshold_flag"),
    ALL,
    "OK OK OK OK COND n/a",
    f"the assignment's core metric on one channel's own data; {DISCLOSED}; the robust z gate is an "
    "internal filter, never displayed; quality flags suppress unreliable ratios",
    displayed=True,
    covers=_fields("VideoMetrics", "outlier flags"),
)
feature(
    15,
    "analytics.format_segment",
    "06",
    "Length band, Shorts flag and format segment",
    YT,
    ("threshold_flag",),
    ALL,
    "OK OK OK OK COND n/a",
    "Shorts by duration and API fields only, no visual inference",
    displayed=False,
    covers=[
        *_fields("VideoMetrics", "segment segment_evidence format_key"),
        *_fields("VideoTraits", "segment length_band format_key"),
    ],
)
feature(
    16,
    "analytics.text_traits",
    "06",
    "Title and description traits: length, question, number, how-to, year, brackets, links, CTA, chapters; "
    "re-upload grouping",
    YT,
    ("lexical_match", "count"),
    ALL,
    "OK OK OK OK COND n/a",
    "regex and lexical rules only; duplicate re-uploads grouped by fuzzy title match within one channel",
    displayed=False,
    covers=[
        *_fields("VideoTraits", "video_id traits_version title description"),
        "VideoMetrics.duplicate_group_id",
    ],
)
feature(
    17,
    "analytics.thumbnail_traits",
    "06",
    "Thumbnail traits: text present, word count, contrast, brightness, colourfulness",
    YT,
    ("image_simple", "count"),
    ALL,
    "OK OK OK OK COND OK",
    "OCR text boxes and pixel statistics only; no face, object or person detection; image bytes are not "
    "persisted, only a content hash",
    displayed=False,
    covers=[
        "VideoTraits.thumbnail",
        *_fields(
            "ThumbnailTraits",
            "video_id source_key source_w source_h content_sha256 letterbox_cropped pillarbox_detected "
            "text_present word_count word_count_band text_box_count text_area_ratio largest_text_height_ratio "
            "brand_text_only script_detected ocr_engine ocr_model ocr_confidence_mean brightness "
            "brightness_band rms_contrast contrast_band colorfulness colorfulness_band dominant_colour_count "
            "traits_version computed_at",
        ),
    ],
)
feature(
    18,
    "analytics.publish_timing",
    "06",
    "Publish day and hour",
    YT,
    ("raw",),
    ALL,
    "OK OK OK OK COND n/a",
    "in the client's timezone (or UTC); no audience timezone inference",
    displayed=False,
    covers=["VideoTraits.timing"],
)
feature(
    19,
    "analytics.comment_signals",
    "06",
    "CommentSignals: buyer-question mentions, question count, purchase-intent keywords",
    YT,
    ("lexical_match", "count", "ratio"),
    ALL,
    "OK OK OK FLAG COND n/a",
    "matched against the client's own buyer questions; no commenter attributes; aggregates only; "
    "no health-status inference",
    displayed=True,
    covers=_fields(
        "CommentSignals",
        "video_id as_of comment_count_api sampled_n eligible_n sample_coverage question_n question_share "
        "intent_counts buyer_intent_n buyer_intent_share unanswered_question_n unanswered_question_share "
        "owner_reply_visible_n lexicon_version sufficient matched_buyer_questions excerpts",
    ),
    review_note="in healthcare (Apollo) comments can reveal a commenter's condition; signals count "
    "client-defined buyer questions and intent words only, never a person's health status, and excerpts "
    "are off for healthcare and finance. Reviewer to confirm this stays clear of derived-metrics category 4.",
)
feature(
    20,
    "analytics.trait_lift",
    "06",
    "TraitLift over outliers vs non-outliers",
    YT,
    ("count", "ratio", "quantile", "statistical_test"),
    PEERS,
    "OK OK OK OK COND n/a",
    "ratio of proportions with a minimum support of 3 outliers across 2 channels; p, q and intervals are "
    "internal filters, never displayed as metrics",
    displayed=True,
    covers=[
        "ChannelScorecard.top_traits",
        *_fields(
            "TraitLift",
            "scope window_days as_of comparison_age_days comparison_method trait_key trait_value interaction "
            "n_with n_without outliers_with outliers_without outlier_rate_with outlier_rate_without "
            "rate_with_wilson lift lift_smoothed lift_ci channel_balanced_lift p_value q_value "
            "outlier_channels_with max_channel_share ratio_quantiles_with reference_video_ids status "
            "redundant_with",
        ),
    ],
)
feature(
    21,
    "analytics.channel_scorecard",
    "06",
    "ChannelScorecard evidence sheet: cadence, median ratio, outlier rate, consistency, Shorts share",
    YT,
    ("count", "median", "ratio", "dispersion"),
    ALL,
    "OK OK OK OK COND n/a",
    "an evidence sheet of separate raw stats side by side; no channel score, rank or leaderboard (99 S3)",
    displayed=True,
    covers=_fields(
        "ChannelScorecard",
        "channel_id client_id role window_days as_of uploads_in_window uploads_per_week gap_cv "
        "longest_gap_days days_since_last_upload segment_mix median_views_at_age median_ratio "
        "outlier_hit_rate outlier_hit_rate_wilson median_engagement_rate data_sufficiency",
    ),
)
feature(
    22,
    "analytics.own_performance",
    "06",
    "OwnPerformanceReport: own videos vs own baseline; Analytics side panel",
    ("yt_data_public", "yt_analytics_oauth"),
    ("median", "ratio", "quantile", "count"),
    OWN,
    "OK COND OK OK COND n/a",
    "public-stat metrics and OAuth metrics are shown in separate labelled panels, never in one number",
    displayed=True,
    covers=_fields(
        "OwnPerformanceReport",
        "client_id channel_id window_days as_of comparison_age_days method_mix videos median_ratio_window "
        "median_ratio_prev_window best worst by_format by_topic by_trait consistency conversion "
        "sufficiency_messages",
    ),
)
feature(
    23,
    "analytics.trending",
    "06/02",
    "TrendingReport: cached niche search plus same-age velocity",
    YT,
    ("ratio", "count"),
    PEERS,
    "OK OK OK OK COND n/a",
    "trending means the niche's cached search results ranked by same-age velocity; the mostPopular chart "
    "and its category filter are not used (99 S4)",
    displayed=True,
    covers=_fields("TrendingReport", "client_id window_days as_of items rising_topics sufficiency_messages"),
)
feature(
    24,
    "topics.lexical_match",
    "05",
    "TopicMatch lexical score",
    ("yt_data_public", "client_input"),
    ("lexical_match",),
    ALL,
    "OK COND OK COND COND n/a",
    "matches video text to the client's own TopicRefs, never to categories; the score is internal",
    displayed=False,
    covers=["VideoTraits.topics"],
)
feature(
    25,
    "topics.embedding_similarity",
    "05",
    "TopicMatch embedding similarity",
    ("yt_data_public", "client_input"),
    ("embedding_similarity",),
    ALL,
    "FLAG COND OK COND COND n/a",
    "cosine similarity of video text to client-defined refs only; disclosed; low-confidence matches "
    "queued for review",
    displayed=False,
    review_note="not simple maths on API numbers: it is text matching against the client's own keywords "
    "and buyer questions, and the assignment asks for such matching to be flagged. Reviewer to confirm.",
)
feature(
    26,
    "topics.llm_adjudication",
    "05",
    "TopicMatch LLM adjudication",
    ("yt_data_public", "client_input", "llm"),
    ("llm_judgement",),
    ALL,
    "FLAG COND OK FLAG COND n/a",
    "the model may only pick ids from the client's list or 'none', with a verified quote; logged and "
    "reviewable; never free clustering",
    displayed=False,
    review_note="an LLM decides whether a video is about a client topic (G1), and a model judging "
    "aboutness is close to category inference (G4); it is constrained to the client's own ids and every "
    "decision is auditable. Reviewer to confirm both cells.",
)
feature(
    27,
    "recommend.topic_coverage",
    "05/07",
    "Topic coverage and gap counts",
    YT,
    ("count",),
    ALL,
    "OK OK OK OK COND n/a",
    "counts of matched videos per client topic and channel",
    displayed=True,
    covers=["ChannelScorecard.topic_coverage"],
)
feature(
    28,
    "website.business_profile",
    "03",
    "BusinessProfile: offerings, value props, locations, conversion actions",
    ("website", "llm"),
    ("llm_judgement",),
    OWN,
    "n/a OK OK OK n/a n/a",
    "non-YouTube source with its own retention (03); never combined into YouTube metrics",
    displayed=False,
    retention_days=None,
)
feature(
    29,
    "website.buyer_questions",
    "03",
    "Buyer questions",
    ("website", "client_input"),
    ("raw",),
    OWN,
    "n/a OK OK OK n/a n/a",
    "client-confirmed; Spotlit AI-answer tracking is deferred (99 G12)",
    displayed=False,
    retention_days=None,
)
feature(
    30,
    "website.freshness_profile",
    "03/08",
    "FreshnessProfile",
    ("web_search",),
    ("count",),
    OWN,
    "n/a OK OK OK n/a n/a",
    "counts of dated sources per topic; web data only",
    displayed=False,
    retention_days=None,
)
feature(
    31,
    "competitors.candidate_evidence",
    "04",
    "Competitor candidate evidence: web mentions, YouTube keyword overlap, ownership verification",
    ("web_search", "yt_data_public", "llm"),
    ("count", "lexical_match", "threshold_flag"),
    ("competitor",),
    "COND FLAG OK OK COND n/a",
    "web and YouTube evidence are separate fields; candidates pass explicit threshold rules and human "
    "confirmation; no blended or channel score (99 S3)",
    displayed=False,
    review_note="web and YouTube evidence both decide which channels are proposed; they are kept as "
    "separate fields behind threshold rules and a human confirms, so no merged number exists. Reviewer to "
    "confirm selection by two sources is not a combined metric.",
)
feature(
    32,
    "recommend.card_ordering",
    "07",
    "Recommendation card ordering: evidence, fit, gap, intent, recency x penalties",
    ("yt_data_public", "client_input"),
    ("rank",),
    ALL,
    "COND FLAG OK OK COND n/a",
    "the assignment asks for cards scored on evidence, fit and buyer intent; the composite orders cards "
    "only (never channels), is never displayed, and every displayed figure stays single-source and labelled",
    displayed=False,
    covers=[
        "ScoreBreakdown.components",
        "ScoreBreakdown.weights",
        "ScoreBreakdown.penalties",
        "ScoreBreakdown.total",
        *_fields("ScoreBreakdown.components", "evidence fit gap intent recency"),
        *_fields("ScoreBreakdown.penalties", "contradiction feedback single_channel staleness"),
    ],
    review_note="the internal ordering multiplies YouTube evidence with client fit and buyer intent; under "
    "D-POL-7 a composite may order cards because it ranks ideas, not channels, and is never shown. Reviewer "
    "to confirm.",
)
feature(
    33,
    "recommend.gap_analysis",
    "07",
    "Gap analysis: competitors cover it, you don't",
    YT,
    ("count",),
    ALL,
    "OK OK OK OK COND n/a",
    "counts of matching uploads per channel, shown side by side",
    displayed=True,
)
feature(
    34,
    "recommend.expected_range",
    "07",
    "expected_range_vs_baseline",
    YT,
    ("quantile", "ratio"),
    ALL,
    "OK OK OK OK COND n/a",
    "a range from reference ratio quantiles times the client baseline, labelled 'indicative', never a "
    "promise; no website data",
    displayed=True,
)
feature(
    35,
    "recommend.confidence_label",
    "07",
    "Confidence label",
    YT,
    ("count", "threshold_flag"),
    ALL,
    "OK OK OK OK COND n/a",
    "High needs 5+ outliers across 3+ channels; first-mover cards stay Low",
    displayed=True,
)
feature(
    36,
    "recommend.creator_list",
    "07",
    "Creator list: relevance, performance vs own median, consistency",
    YT,
    ("lexical_match", "median", "dispersion", "threshold_flag"),
    ("niche",),
    "OK OK OK OK COND n/a",
    "creators are filtered by explicit threshold rules and sorted by one transparent, user-selectable raw "
    "metric; no composite creator score, no audience, reach or demographic estimates (99 S3)",
    displayed=True,
)
feature(
    37,
    "recommend.collaboration_labels",
    "07",
    "Collaboration labels: mention vs disclosed paid promotion",
    YT,
    ("lexical_match", "raw", "count"),
    ALL,
    "OK OK OK OK COND n/a",
    "uses the creator's own disclosure (paidProductPlacementDetails, unverified) and description mentions; "
    "no revenue or deal-value estimates",
    displayed=False,
    covers=_fields("ChannelScorecard", "paid_placement_share collaborations"),
)
feature(
    38,
    "recommend.no_copy_guard",
    "07",
    "No-copy title similarity and novelty vs own uploads",
    YT,
    ("lexical_match",),
    ALL,
    "OK OK OK OK COND n/a",
    "internal guard against copying competitor or own recent titles",
    displayed=False,
)
feature(
    39,
    "news.signal",
    "08",
    "NewsSignal: recency, source count, credibility tier, relevance judgement, safety",
    ("web_search", "llm", "client_input"),
    ("count", "llm_judgement"),
    OWN,
    "n/a OK OK OK n/a n/a",
    "web data only; entities verified verbatim in articles; health stories go to human review",
    displayed=True,
    retention_days=NEWS_DAYS,
    covers=_fields(
        "NewsSignal",
        "id client_id story_id profile_version judgement credibility aliases is_competitor_entity "
        "topic_ref_ids first_seen_at sources verification_status superseded_by half_life_hours expires_at "
        "safety status suppression_reason created_at",
    ),
)
feature(
    40,
    "news.youtube_demand",
    "08",
    "YouTube demand check for a news keyword",
    YT,
    ("count", "raw"),
    ("niche",),
    "OK COND OK OK COND n/a",
    "shown beside the news evidence, never multiplied into one opportunity score",
    displayed=True,
    covers=["NewsSignal.demand"],
)
feature(
    41,
    "news.opportunity_ordering",
    "08",
    "news_opportunity ordering",
    ("web_search", "yt_data_public", "llm"),
    ("rank",),
    OWN,
    "COND FLAG OK OK COND n/a",
    "lexicographic order of relevance, credibility, freshness and saturation; no blended priority number",
    displayed=False,
    stored=False,
    covers=_fields("NewsSignal", "relevance_score urgency"),
    review_note="news and YouTube demand both influence which news card comes first; ordering is "
    "lexicographic with each figure shown separately, so no merged metric exists (D-POL-7). Reviewer to "
    "confirm.",
)
feature(
    42,
    "refresh.warranted_decision",
    "09",
    "Refresh-warranted decision",
    ("web_search", "yt_data_public"),
    ("threshold_flag",),
    OWN,
    "COND COND OK OK COND n/a",
    "a rule whose conditions are each shown separately; no fused metric",
    displayed=False,
    covers=_fields("NewsSignal", "refresh_keywords refresh_eligible"),
)
feature(
    43,
    "refresh.description_insertions",
    "09",
    "Description keyword insertions",
    ("llm", "web_search", "yt_analytics_oauth"),
    ("text_generation",),
    OWN,
    "n/a OK OK OK COND n/a",
    "additive diff only (at most 3 single-line inserts); search terms only for an OAuth-connected own channel",
    displayed=False,
)
feature(
    44,
    "llm.card_wording",
    "10",
    "Card wording with grounding validation",
    ("llm", "yt_data_public"),
    ("text_generation",),
    ALL,
    "n/a OK OK OK COND n/a",
    "the writer cannot add numbers, videos or names absent from the evidence pack; cards carrying API data "
    "expire or are redacted at 28 d",
    displayed=False,
)
feature(
    45,
    "llm.critic_score",
    "10",
    "LLM critic score",
    ("llm",),
    ("llm_judgement",),
    NONE,
    "n/a OK OK OK n/a n/a",
    "internal gate signal, never displayed",
    displayed=False,
    stored=False,
)
feature(
    46,
    "evaluation.backtest_metrics",
    "12",
    "Backtest metrics",
    ("yt_data_public", "internal"),
    ("ratio", "quantile", "statistical_test"),
    PEERS,
    "OK OK OK OK COND n/a",
    "video-level outcome rows and creator channel ids are deleted or nulled at 28 d; only aggregates are kept",
    displayed=True,
)
feature(
    47,
    "evaluation.outlier_precision",
    "12",
    "Outlier precision/recall and threshold curve",
    ("yt_data_public", "internal"),
    ("ratio", "statistical_test"),
    ALL,
    "OK OK OK OK COND n/a",
    "detector ratios and video ids in label tasks are nulled at 28 d; labels and aggregates are kept",
    displayed=True,
)
feature(
    48,
    "evaluation.review_ratings",
    "12",
    "Human review ratings",
    ("internal",),
    ("mean", "statistical_test"),
    NONE,
    "n/a n/a OK OK COND n/a",
    "card snapshots in review items are nulled at 28 d; ratings are human judgements and are kept",
    displayed=True,
    retention_days=None,
)
feature(
    49,
    "evaluation.ops_metrics",
    "12",
    "Ops metrics: quota units, latency, tokens, cost",
    ("internal",),
    ("count", "sum", "quantile"),
    NONE,
    "n/a n/a n/a n/a n/a n/a",
    "internal telemetry; no API data",
    displayed=True,
    retention_days=LEDGER_DAYS,
)
feature(
    50,
    "answer_citations.cited_in_answers",
    "03/07",
    "Cited in AI answers: YouTube videos a web answer search cites per buyer question (own, competitor, other)",
    ("web_search", "yt_data_public", "client_input"),
    ("count", "lexical_match"),
    ALL,
    "COND FLAG OK OK COND n/a",
    "web answer sources are matched to video ids and resolved through videos.list; counts of questions are "
    "shown side by side with YouTube data, never merged into a YouTube metric; at most 10 questions a week per "
    "client; citations and channel ids are deleted at 28 d; " + DISCLOSED,
    displayed=True,
    review_note="a web search answer decides which videos count as cited, and YouTube supplies each video's "
    "channel; the two sources meet only in a per-question owner label, never in one number. Reviewer to confirm "
    "this side-by-side reading of G2.",
)
