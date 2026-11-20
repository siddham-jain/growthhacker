"""a synthetic niche with a known planted pattern, for testing the backtest without the analytics engine."""

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np

from app.domain.common import FormatSegment
from app.domain.recommendations import Lens, MatchSpec, RecType
from app.evaluation.backtest.frame import ClientEvaluation
from app.evaluation.backtest.models import ArmSpec, BacktestConfig, OutcomeVideo
from app.evaluation.backtest.scoring import ScoringSettings
from app.pipeline.contracts import Market, RunConfigSnapshot

T = datetime(2026, 11, 26, 23, 59, tzinfo=UTC)
TOPICS = [f"tr_topic{n:06d}" for n in range(6)]
PLANTED = TOPICS[0]
FLOORS = {
    "min_universe_videos_client": 20,
    "min_matched_videos_client": 5,
    "min_universe_videos_pooled": 100,
    "min_matched_videos_pooled": 15,
    "min_channels_pooled": 10,
}


def spec(
    spec_id: str,
    *,
    topics: list[str] | None = None,
    question: bool | None = None,
    segment: FormatSegment | None = None,
    channel_id: str | None = None,
    rec_type: RecType = RecType.video_idea,
    source: str = "system",
    lens: list[Lens] | None = None,
) -> MatchSpec:
    title = {} if question is None else {"is_question": question}
    topics = topics or []
    return MatchSpec(
        spec_id=spec_id,
        rec_type=rec_type,
        topic_ids=topics,
        format=segment,
        title_traits=title,
        channel_id=channel_id,
        lens=lens or [Lens.views],
        specificity=len(topics) + len(title) + (segment is not None) + (channel_id is not None),
        source=source,
    )


def config(client_id: uuid.UUID, **overrides) -> BacktestConfig:
    values = {
        "client_id": client_id,
        "workspace_id": client_id,
        "freeze_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "as_of": T,
        "window_days": 30,
        "horizon_days": 10,
        "outcome_age_days": 7,
        "k_per_type": {"video_idea": 4},
        "random_replicates": 200,
        "bootstrap_resamples": 300,
        "alpha": 0.05,
        "seed": 7,
        "outlier_threshold": 3.0,
        "include_own_channel": False,
        "floors": FLOORS,
        "max_missing_share": 0.2,
        "max_usd": 5.0,
        "max_quota_units": 1000,
        "code_version": "test",
        "snapshot": RunConfigSnapshot(
            workspace_id=client_id,
            client_id=client_id,
            config_version=1,
            config_hash="h",
            window_days=30,
            topic_set_version=1,
            market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
        ),
        "universe": [],
        "own_channel_id": None,
        "frozen_baselines": {},
        "leakage_canary_passed": True,
        "pit_level": "strict",
    }
    return BacktestConfig(**(values | overrides))


def settings_for(configs: list[BacktestConfig]) -> ScoringSettings:
    first = configs[0]
    return ScoringSettings(
        resamples=first.bootstrap_resamples,
        alpha=first.alpha,
        seed=first.seed,
        floors=dict(first.floors),
        few_clusters=5,
        max_missing_share=first.max_missing_share,
    )


def outcome(
    client_id: uuid.UUID,
    channel_id: str,
    index: int,
    topic: str,
    question: bool,
    ratio: float,
    status: str = "complete",
) -> OutcomeVideo:
    return OutcomeVideo(
        video_id=f"v{channel_id[-4:]}{index:06d}",
        channel_id=channel_id,
        client_id=client_id,
        published_at=T + timedelta(hours=1 + index),
        segment="long_form",
        views_at_age=int(1000 * ratio),
        views_at_age_method="observed",
        baseline_at_t=1000.0 if status == "complete" else None,
        baseline_method="true",
        ratio=ratio if status == "complete" else None,
        is_outlier=ratio >= 3.0 if status == "complete" else None,
        detector_ratio=ratio,
        detector_is_outlier=ratio >= 3.0,
        status=status,
        traits={
            "title.is_question": "true" if question else "false",
            "segment": "long_form",
            "length_band": "4-8m",
        },
        topic_ids=frozenset({topic}),
    )


def niche(
    seed: int,
    *,
    planted_rate: float = 0.6,
    base_rate: float = 0.06,
    clients: int = 3,
    channels: int = 6,
    videos: int = 15,
    replicates: int = 200,
) -> tuple[list[ClientEvaluation], list[BacktestConfig]]:
    rng = np.random.default_rng(seed)
    evaluations, configs = [], []
    for c in range(clients):
        client_id = uuid.UUID(int=c + 1)
        outcomes = []
        for ch in range(channels):
            channel_id = f"UC{c:02d}{ch:020d}"
            for i in range(videos):
                topic = TOPICS[int(rng.integers(0, len(TOPICS)))]
                question = bool(rng.random() < 0.5)
                p = planted_rate if topic == PLANTED and question else base_rate
                hit = rng.random() < p
                ratio = (
                    float(3.2 * np.exp(abs(rng.normal(0, 0.3))))
                    if hit
                    else float(np.exp(rng.normal(-0.2, 0.4)))
                )
                ratio = min(ratio, 2.9) if not hit else ratio
                outcomes.append(outcome(client_id, channel_id, i, topic, question, ratio))
        system = [
            ArmSpec(arm="system", spec=spec("sys-a", topics=[PLANTED], question=True), confidence="High"),
            ArmSpec(
                arm="system",
                spec=spec("sys-b", topics=[PLANTED], question=True, segment=FormatSegment.long_form),
                confidence="Medium",
                expected_low=2.0,
                expected_high=6.0,
            ),
        ]
        copy_top = [
            ArmSpec(arm="copy_top", spec=spec("cp-0", topics=[TOPICS[1]], question=False, source="copy_top")),
            ArmSpec(
                arm="copy_top",
                spec=spec(
                    "cp-1",
                    topics=[TOPICS[2]],
                    question=False,
                    segment=FormatSegment.long_form,
                    source="copy_top",
                ),
            ),
        ]
        random = []
        for r in range(replicates):
            for k in range(2):
                random.append(
                    ArmSpec(
                        arm="random",
                        replicate=r,
                        spec=spec(
                            f"random:{r}:{k}",
                            topics=[TOPICS[int(rng.integers(0, len(TOPICS)))]],
                            question=bool(rng.random() < 0.5),
                            source="random",
                        ),
                    )
                )
        evaluations.append(
            ClientEvaluation(
                eval_id=str(uuid.uuid4()),
                client_id=str(client_id),
                specs=system + copy_top + random,
                outcomes=outcomes,
                replicates=replicates,
            )
        )
        configs.append(config(client_id, random_replicates=replicates))
    return evaluations, configs
