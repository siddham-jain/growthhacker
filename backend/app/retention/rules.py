from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from app.core.config import PolicySettings
from app.core.registry import Registry


class DataClass(StrEnum):
    yt_public_content = "yt_public_content"
    yt_public_stats_series = "yt_public_stats_series"
    yt_public_derived = "yt_public_derived"
    yt_authorized_analytics = "yt_authorized_analytics"
    tenant_derived_from_api = "tenant_derived_from_api"
    tenant_owned = "tenant_owned"
    external_non_youtube = "external_non_youtube"
    ops_ledger = "ops_ledger"
    auth_secret = "auth_secret"


class RetentionAction(StrEnum):
    refresh_or_delete = "refresh_or_delete"
    delete = "delete"
    redact = "redact"
    drop_partition = "drop_partition"
    verify_authorization = "verify_authorization"
    reverify_ids = "reverify_ids"
    cascade = "cascade"
    keep = "keep"


API_DATA_CLASSES = frozenset(
    {
        DataClass.yt_public_content,
        DataClass.yt_public_stats_series,
        DataClass.yt_public_derived,
        DataClass.yt_authorized_analytics,
        DataClass.tenant_derived_from_api,
    }
)
STATS_CLASSES = frozenset({DataClass.yt_public_stats_series, DataClass.yt_public_derived})
CLOCKLESS_ACTIONS = frozenset({RetentionAction.keep, RetentionAction.cascade})


# max_age_days=None binds an observed clock to the youtube policy act-by age (policy.retention_days)
@dataclass(frozen=True)
class RetentionRule:
    name: str
    table: str
    data_class: DataClass
    action: RetentionAction
    clock: str | None = None
    clock_kind: Literal["observed", "deadline"] = "observed"
    max_age_days: int | None = None
    condition: str | None = None
    redact_set: dict[str, str] = field(default_factory=dict)
    parent: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.action in CLOCKLESS_ACTIONS and self.clock is not None:
            raise ValueError(f"{self.name}: '{self.action}' rules have no clock")
        if self.action is RetentionAction.cascade and self.parent is None:
            raise ValueError(f"{self.name}: cascade rules name their parent table")
        needs_clock = self.action not in CLOCKLESS_ACTIONS and not (
            self.action is RetentionAction.delete and self.condition
        )
        if needs_clock and self.clock is None:
            raise ValueError(f"{self.name}: '{self.action}' rules need a clock")
        if self.action is RetentionAction.redact and not (self.redact_set and self.condition):
            raise ValueError(f"{self.name}: redact rules need redact_set and a pending-row condition")

    @property
    def policy_bound(self) -> bool:
        return self.max_age_days is None and self.clock_kind == "observed" and self.clock is not None

    def max_age(self, policy: PolicySettings) -> timedelta | None:
        if self.clock is None or self.clock_kind == "deadline":
            return None
        if self.max_age_days is not None:
            return timedelta(days=self.max_age_days)
        if policy.retention_mode == "derived_metrics_36m" and self.data_class in STATS_CLASSES:
            return timedelta(days=30 * policy.derived_metrics_months)
        return timedelta(days=policy.retention_days)

    def cutoff(self, now: datetime, policy: PolicySettings) -> datetime:
        max_age = self.max_age(policy)
        return now if max_age is None else now - max_age

    def hard_limit(self, policy: PolicySettings) -> timedelta | None:
        if not self.policy_bound or self.data_class not in API_DATA_CLASSES:
            return None
        if policy.retention_mode == "derived_metrics_36m" and self.data_class in STATS_CLASSES:
            return timedelta(days=30 * policy.derived_metrics_months)
        return timedelta(days=policy.max_age_days - 1)


retention_registry: Registry[RetentionRule] = Registry("retention_registry")

DC = DataClass
A = RetentionAction
ACTIVE_RUN_SQL = "status IN ('queued', 'running', 'cancelling')"
INACTIVE_TOKEN_SQL = "oauth_token_id IN (SELECT id FROM oauth_tokens WHERE status <> 'active')"


def _rules() -> list[RetentionRule]:
    def rule(
        table: str, data_class: DataClass, action: RetentionAction, name: str | None = None, **kwargs
    ) -> RetentionRule:
        return RetentionRule(name=name or table, table=table, data_class=data_class, action=action, **kwargs)

    def keep(table: str, data_class: DataClass = DC.tenant_owned, note: str = "") -> RetentionRule:
        return rule(table, data_class, A.keep, note=note)

    def cascade(table: str, data_class: DataClass, parent: str) -> RetentionRule:
        return rule(table, data_class, A.cascade, parent=parent)

    def delete(
        table: str, data_class: DataClass, clock: str, days: int | None = None, **kwargs
    ) -> RetentionRule:
        return rule(table, data_class, A.delete, clock=clock, max_age_days=days, **kwargs)

    def expire(table: str, data_class: DataClass, clock: str = "expires_at", **kwargs) -> RetentionRule:
        return rule(table, data_class, A.delete, clock=clock, clock_kind="deadline", **kwargs)

    def redact(
        table: str, data_class: DataClass, clock: str, sets: dict[str, str], pending: str, **kwargs
    ) -> RetentionRule:
        return rule(table, data_class, A.redact, clock=clock, redact_set=sets, condition=pending, **kwargs)

    def reverify(table: str, clock: str = "ids_verified_at") -> RetentionRule:
        return rule(
            table, DC.tenant_derived_from_api, A.reverify_ids, clock=clock, note="d-pol-11 id re-verification"
        )

    return [
        expire("workspaces", DC.tenant_owned, "purge_after"),
        keep("users", DC.auth_secret),
        keep("workspace_members", DC.auth_secret),
        expire("user_sessions", DC.auth_secret),
        delete("api_keys", DC.auth_secret, "revoked_at", 90),
        delete("audit_log", DC.ops_ledger, "at", 400),
        delete("deletion_requests", DC.ops_ledger, "completed_at", 400),
        delete("clients", DC.tenant_owned, "deleted_at", 30),
        rule(
            "oauth_tokens",
            DC.yt_authorized_analytics,
            A.verify_authorization,
            clock="authorization_verified_at",
            max_age_days=30,
        ),
        expire("idempotency_keys", DC.tenant_owned),
        rule(
            "yt_channels",
            DC.yt_public_content,
            A.refresh_or_delete,
            clock="LEAST(metadata_fetched_at, stats_fetched_at)",
        ),
        rule("yt_channel_snapshots", DC.yt_public_stats_series, A.drop_partition, clock="captured_at"),
        expire("yt_channel_resolutions", DC.yt_public_content),
        rule(
            "yt_videos",
            DC.yt_public_content,
            A.refresh_or_delete,
            clock="LEAST(metadata_fetched_at, stats_fetched_at)",
        ),
        delete(
            "yt_videos",
            DC.yt_public_content,
            "first_missing_at",
            1,
            name="yt_videos:removed",
            condition="availability = 'removed'",
        ),
        rule("yt_video_snapshots", DC.yt_public_stats_series, A.drop_partition, clock="slot_at"),
        cascade("yt_snapshot_schedule", DC.yt_public_content, "yt_videos"),
        delete("yt_comments", DC.yt_public_content, "fetched_at"),
        delete("yt_comment_fetch", DC.yt_public_content, "fetched_at"),
        expire("yt_search_cache", DC.yt_public_content),
        cascade("yt_search_result", DC.yt_public_content, "yt_search_cache"),
        delete("yt_http_etag", DC.yt_public_content, "stored_at"),
        delete("yt_demand_checks", DC.yt_public_content, "checked_at"),
        delete("creator_collaborations", DC.yt_public_derived, "detected_at"),
        expire("brand_channel_resolutions", DC.yt_public_content),
        delete("yt_video_age_values", DC.yt_public_derived, "source_oldest_at"),
        delete("yt_video_metrics", DC.yt_public_derived, "source_oldest_at"),
        delete("yt_channel_baselines", DC.yt_public_derived, "source_oldest_at"),
        delete("yt_video_outliers", DC.yt_public_derived, "source_oldest_at"),
        delete("yt_video_traits", DC.yt_public_derived, "source_oldest_at"),
        delete("yt_thumbnail_traits", DC.yt_public_derived, "source_verified_at"),
        delete("yt_channel_recurring_text", DC.yt_public_derived, "source_oldest_at"),
        delete("yt_comment_signals", DC.yt_public_derived, "source_oldest_at"),
        delete("channel_boilerplate", DC.yt_public_content, "computed_at"),
        delete("video_prepared_text", DC.yt_public_content, "prepared_at"),
        delete("text_embeddings", DC.yt_public_content, "created_at"),
        delete("yt_quota_ledger", DC.ops_ledger, "occurred_at", 400),
        delete("quota_cache_hits_daily", DC.ops_ledger, "pt_day", 400),
        delete("provider_usage_ledger", DC.ops_ledger, "occurred_at", 400),
        delete("pipeline_runs", DC.tenant_owned, "finished_at", 180, condition=f"NOT ({ACTIVE_RUN_SQL})"),
        cascade("stage_runs", DC.tenant_owned, "pipeline_runs"),
        delete("run_events", DC.tenant_derived_from_api, "created_at"),
        expire("run_artifacts", DC.tenant_derived_from_api),
        expire("run_artifact_blobs", DC.tenant_derived_from_api),
        keep("client_schedules"),
        keep("active_result_sets"),
        rule("own_analytics_daily", DC.yt_authorized_analytics, A.delete, condition=INACTIVE_TOKEN_SQL),
        rule("own_traffic_source", DC.yt_authorized_analytics, A.delete, condition=INACTIVE_TOKEN_SQL),
        rule("own_search_term", DC.yt_authorized_analytics, A.delete, condition=INACTIVE_TOKEN_SQL),
        rule("own_video_metrics", DC.yt_authorized_analytics, A.delete, condition=INACTIVE_TOKEN_SQL),
        keep("websites"),
        # the stored acquisition carries page and channel text; the crawl run row itself is kept
        redact(
            "crawl_runs",
            DC.tenant_derived_from_api,
            "started_at",
            {"acquisition_gz": "NULL"},
            "acquisition_gz IS NOT NULL",
        ),
        keep("url_inventory"),
        delete(
            "crawl_frontier",
            DC.tenant_owned,
            "(SELECT finished_at FROM crawl_runs WHERE crawl_runs.id = crawl_frontier.crawl_run_id)",
            7,
        ),
        delete("crawled_pages", DC.external_non_youtube, "fetched_at", 180),
        delete(
            "crawled_pages",
            DC.yt_public_content,
            "fetched_at",
            name="crawled_pages:youtube_channel",
            condition="source_kind = 'youtube_channel'",
        ),
        expire("page_blobs", DC.external_non_youtube),
        delete("page_facts", DC.external_non_youtube, "superseded_at", 90),
        delete(
            "page_facts",
            DC.yt_public_content,
            "fetched_at",
            name="page_facts:youtube_channel",
            condition="source_kind = 'youtube_channel'",
        ),
        keep("business_profiles"),
        keep("profile_edits"),
        keep("profile_diffs"),
        keep("profile_tombstones"),
        delete(
            "keywords", DC.tenant_derived_from_api, "api_data_observed_at", condition="status <> 'confirmed'"
        ),
        keep("buyer_questions"),
        keep("competitor_discovery_runs"),
        keep("competitor_candidates"),
        delete("competitor_candidate_sources", DC.external_non_youtube, "retrieved_at", 180),
        redact(
            "competitor_channel_matches",
            DC.tenant_derived_from_api,
            "youtube_fetched_at",
            {"youtube_evidence": "NULL", "youtube_fetched_at": "NULL"},
            "youtube_evidence IS NOT NULL",
        ),
        reverify("competitor_sets"),
        keep("competitor_drift_events"),
        keep("topic_universes"),
        expire("topic_refs", DC.tenant_owned),
        keep("topic_aliases"),
        keep("ref_embeddings"),
        expire("topic_matches", DC.tenant_derived_from_api),
        expire("answer_directness", DC.tenant_derived_from_api),
        expire("llm_adjudications", DC.tenant_derived_from_api),
        reverify("topic_match_overrides"),
        rule(
            "topic_review_items",
            DC.tenant_derived_from_api,
            A.delete,
            condition="video_id NOT IN (SELECT video_id FROM yt_videos)",
        ),
        reverify("topic_eval_labels"),
        redact(
            "recommendations",
            DC.tenant_derived_from_api,
            "api_data_observed_at",
            {"payload": "NULL", "draft": "NULL", "status": "'expired'", "redacted_at": "now()"},
            "redacted_at IS NULL",
        ),
        delete(
            "recommendation_references",
            DC.tenant_derived_from_api,
            "(SELECT api_data_observed_at FROM recommendations WHERE recommendations.id = "
            "recommendation_references.recommendation_id)",
        ),
        redact(
            "recommendation_suppressions",
            DC.tenant_derived_from_api,
            "created_at",
            {"draft": "NULL"},
            "draft IS NOT NULL",
        ),
        delete("recommendation_notices", DC.tenant_derived_from_api, "created_at"),
        delete("white_space_notes", DC.tenant_derived_from_api, "created_at"),
        reverify("recommendation_feedback", "linked_video_verified_at"),
        redact(
            "recommendation_outcomes",
            DC.tenant_derived_from_api,
            "measured_at",
            {"observed_ratio": "NULL"},
            "observed_ratio IS NOT NULL",
        ),
        keep("client_recommend_config"),
        delete("news_queries", DC.external_non_youtube, "orphaned_at", 30),
        keep("news_query_subscriptions"),
        delete("news_query_runs", DC.external_non_youtube, "started_at", 90),
        delete("news_items", DC.external_non_youtube, "fetched_at", 90),
        delete("news_stories", DC.external_non_youtube, "last_published_at", 90),
        keep("source_tiers", DC.external_non_youtube),
        delete("news_judgements", DC.tenant_owned, "created_at", 180),
        delete("news_signals", DC.tenant_owned, "created_at", 180),
        redact(
            "news_signals",
            DC.tenant_derived_from_api,
            "demand_expires_at",
            {"demand": "NULL", "demand_check_id": "NULL", "demand_video_ids": "NULL"},
            "demand IS NOT NULL OR demand_check_id IS NOT NULL OR demand_video_ids IS NOT NULL",
            name="news_signals:demand",
            clock_kind="deadline",
        ),
        delete("news_review_queue", DC.tenant_owned, "created_at", 180),
        delete(
            "description_refreshes",
            DC.tenant_derived_from_api,
            "generated_at",
            condition="status NOT IN ('applied_claimed', 'applied_detected')",
        ),
        redact(
            "description_refreshes",
            DC.tenant_derived_from_api,
            "generated_at",
            {"triggers": "'[]'::jsonb", "advisories": "'[]'::jsonb", "redacted_at": "now()"},
            "redacted_at IS NULL AND status IN ('applied_claimed', 'applied_detected')",
            name="description_refreshes:applied",
        ),
        delete("refresh_eligibility", DC.tenant_derived_from_api, "computed_at"),
        delete("refresh_impact", DC.tenant_derived_from_api, "computed_at"),
        delete("link_check_cache", DC.external_non_youtube, "checked_at", 7),
        expire("llm_result_cache", DC.tenant_derived_from_api),
        delete("recommendation_copy", DC.tenant_derived_from_api, "created_at"),
        expire("llm_debug_payloads", DC.tenant_derived_from_api),
        # a scored config can name client channels and videos; it keeps only its hash and spend after day 28
        redact(
            "eval_config",
            DC.tenant_derived_from_api,
            "finished_at",
            {"config": "'{}'::jsonb"},
            "finished_at IS NOT NULL AND config <> '{}'::jsonb",
        ),
        redact(
            "eval_freeze",
            DC.tenant_derived_from_api,
            "expires_at",
            {"bundle_uri": "NULL", "channel_ids": "'{}'::text[]", "status": "'purged'", "purged_at": "now()"},
            "purged_at IS NULL",
            clock_kind="deadline",
        ),
        redact(
            "eval_pattern_spec",
            DC.tenant_derived_from_api,
            "expires_at",
            {"channel_id": "NULL"},
            "channel_id IS NOT NULL",
            clock_kind="deadline",
        ),
        expire("eval_outcome_video", DC.tenant_derived_from_api),
        keep("eval_result"),
        redact(
            "label_task",
            DC.tenant_derived_from_api,
            "expires_at",
            {"detector_ratio": "NULL", "video_id": "NULL", "channel_id": "NULL"},
            "video_id IS NOT NULL OR channel_id IS NOT NULL OR detector_ratio IS NOT NULL",
            clock_kind="deadline",
        ),
        keep("label"),
        keep("label_gold"),
        keep("outlier_eval_result"),
        redact(
            "review_item",
            DC.tenant_derived_from_api,
            "expires_at",
            {"card_snapshot": "NULL"},
            "card_snapshot IS NOT NULL",
            clock_kind="deadline",
        ),
        keep("review_rating"),
        keep("gate_tuning_proposal"),
        delete("ops_daily_rollup", DC.ops_ledger, "day", 400),
        delete("alert_event", DC.ops_ledger, "fired_at", 90),
    ]


def register_default_rules() -> None:
    for retention_rule in _rules():
        retention_registry.add(retention_rule.name, retention_rule)


def rules_for_table(table: str) -> list[RetentionRule]:
    return [rule for rule in retention_registry.all().values() if rule.table == table]


register_default_rules()
