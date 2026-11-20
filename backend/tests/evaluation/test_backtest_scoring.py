import dataclasses

from app.evaluation.backtest.render import render_backtest
from app.evaluation.backtest.scoring import score
from tests.evaluation.backtest_fixtures import T, niche, outcome, settings_for


def run(evaluations, configs):
    return score(evaluations, configs, [{"pit_level": "strict"}] * len(configs), settings_for(configs), T)


def test_planted_pattern_is_recovered_and_beats_both_naive_arms() -> None:
    evaluations, configs = niche(seed=1)
    report = run(evaluations, configs)
    pooled = {m.arm: m for m in report.arms if m.scope == "pooled"}
    assert report.verdict == "supported"
    assert pooled["system"].outlier_rate > 0.4
    assert pooled["copy_top"].outlier_rate < 0.15
    primary = report.comparisons[0]
    assert primary.primary and primary.ci[0] > 0
    lift = pooled["system"].lift_vs_base
    assert 3.0 < lift < 9.0
    random_test = next(c for c in report.comparisons if c.b == "random")
    assert random_test.p_value < 0.05
    assert report.random_percentile > 0.9
    assert report.calibration["by_confidence"]["High"]["videos"] > 0
    assert report.mde["min_detectable_lift"] is not None


def test_no_planted_effect_is_rarely_called_supported() -> None:
    supported = 0
    for seed in range(20):
        evaluations, configs = niche(seed=100 + seed, planted_rate=0.12, base_rate=0.12, replicates=100)
        supported += run(evaluations, configs).verdict == "supported"
    assert supported <= 1


def test_small_samples_are_inconclusive_with_the_reason() -> None:
    evaluations, configs = niche(seed=3, clients=1, channels=4, videos=10)
    report = run(evaluations, configs)
    assert report.verdict == "inconclusive_underpowered"
    assert any("complete post-T videos" in item for item in report.limitations)


def test_too_many_missing_day7_values_invalidate_the_run() -> None:
    evaluations, configs = niche(seed=4)
    first = evaluations[0]
    client = first.outcomes[0].client_id
    missing = [
        outcome(client, "UCmissing00000000000000", i, "tr_topic000000", True, 1.0, status="missing_snapshot")
        for i in range(200)
    ]
    evaluations[0] = dataclasses.replace(first, outcomes=first.outcomes + missing)
    report = run(evaluations, configs)
    assert report.verdict == "invalid"
    assert report.missingness["missing_snapshot"] == 200


def test_a_failed_leakage_canary_invalidates_the_run() -> None:
    evaluations, configs = niche(seed=5)
    evaluations[1] = dataclasses.replace(evaluations[1], invalid_reasons=["leakage canary failed"])
    assert run(evaluations, configs).verdict == "invalid"


def test_abstaining_client_is_reported_not_failed() -> None:
    evaluations, configs = niche(seed=6)
    quiet = evaluations[2]
    evaluations[2] = dataclasses.replace(quiet, specs=[s for s in quiet.specs if s.arm != "system"])
    report = run(evaluations, configs)
    assert report.abstentions == [quiet.client_id]


def test_scoring_is_deterministic_and_renders_without_video_ids() -> None:
    evaluations, configs = niche(seed=8)
    first, second = run(evaluations, configs), run(evaluations, configs)
    assert first.model_dump() == second.model_dump()
    markdown = render_backtest(first)
    assert "# Backtest report" in markdown and "Copy the most-viewed recent video" in markdown
    assert all(o.video_id not in markdown for e in evaluations for o in e.outcomes)
    assert "Calculated by GrowthHacker" in markdown
