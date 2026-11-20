from collections.abc import Mapping

from app.domain.runs import RunKind, RunStatus

MESSAGE_MAX_CHARS = 200

STAGE_LABELS: Mapping[str, str] = {
    "resolve_inputs": "Checking your website and channel",
    "crawl_site": "Reading your website",
    "prefetch_own_channel": "Fetching your recent uploads",
    "build_profile": "Building your business profile",
    "discover_competitors": "Finding competitor channels",
    "seed_topic_refs": "Drafting keywords and buyer questions",
    "finish_onboarding": "Preparing your setup",
    "snapshot_config": "Locking in your setup",
    "ingest": "Fetching channel videos",
    "niche_discovery": "Scanning your niche",
    "own_analytics": "Reading your YouTube Analytics",
    "news": "Checking the news radar",
    "enrich": "Reading thumbnails and comments",
    "topics": "Matching videos to your topics",
    "analytics": "Finding outliers and patterns",
    "recommend": "Choosing what to recommend",
    "write_cards": "Writing recommendation cards",
    "refresh": "Reviewing your video descriptions",
    "finalize": "Publishing results",
    "frozen_corpus": "Loading the frozen corpus",
    "record_backtest": "Recording backtest results",
}

RUN_LABELS: Mapping[RunKind, str] = {
    RunKind.onboarding: "Onboarding",
    RunKind.analysis: "Analysis",
    RunKind.daily_refresh: "Daily refresh",
    RunKind.cards: "Card writing",
    RunKind.news_scan: "News scan",
    RunKind.recrawl: "Website re-read",
    RunKind.backtest: "Backtest",
}


def clip(message: str) -> str:
    return message if len(message) <= MESSAGE_MAX_CHARS else message[: MESSAGE_MAX_CHARS - 1] + "…"


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " ").capitalize())


def run_queued(kind: RunKind) -> str:
    return f"{RUN_LABELS[kind]} queued"


def run_started(kind: RunKind, attempt: int) -> str:
    if attempt > 1:
        return f"Resuming {RUN_LABELS[kind].lower()} after a brief interruption"
    return f"{RUN_LABELS[kind]} started"


def run_finished(kind: RunKind, status: RunStatus) -> str:
    label = RUN_LABELS[kind]
    return {
        RunStatus.succeeded: f"{label} finished",
        RunStatus.partial: f"{label} finished with some gaps",
        RunStatus.failed: f"{label} could not finish",
        RunStatus.cancelled: f"{label} cancelled",
        RunStatus.superseded: f"{label} replaced by a newer run",
    }[status]


def stage_started(stage: str) -> str:
    return clip(f"{stage_label(stage)}…")


def stage_reused(stage: str) -> str:
    return clip(f"{stage_label(stage)}: reused unchanged results")


def stage_retrying(stage: str, attempt: int) -> str:
    return clip(f"{stage_label(stage)}: retrying (attempt {attempt})")


def stage_resuming(stage: str) -> str:
    return clip(f"Resuming from {stage_label(stage)}…")


def stage_finished(stage: str, status: str) -> str:
    suffix = {
        "succeeded": "done",
        "degraded": "done with gaps",
        "skipped": "skipped",
        "failed": "failed",
        "cancelled": "cancelled",
        "interrupted": "interrupted",
    }[status]
    return clip(f"{stage_label(stage)}: {suffix}")


def results_published(windows: list[int]) -> str:
    joined = ", ".join(f"{window}-day" for window in windows)
    return clip(f"New results published ({joined})")


def awaiting_confirmation() -> str:
    return "Your setup is ready to review"
