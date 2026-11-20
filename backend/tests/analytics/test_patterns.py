from dataclasses import replace
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.analytics import AgeMethod, LiftScope
from app.services.analytics.patterns.contingency import MiningUnit, split
from app.services.analytics.patterns.miner import MiningRequest, PatternMiner
from app.services.analytics.settings import PatternSettings
from tests.analytics.factories import AS_OF, channel_id, video_id

REQUEST = MiningRequest(
    scope=LiftScope.competitor_set,
    window_days=30,
    as_of=AS_OF,
    comparison_age_days=7,
    comparison_method=AgeMethod.observed,
)
TRUE, NONE = frozenset({"true"}), frozenset()


def unit(
    channel: int,
    n: int,
    *,
    outlier: bool,
    how_to: bool,
    dominated: bool = False,
    rare: bool = False,
    topic: str | None = None,
    flagged: bool = False,
    ratio: float | None = None,
) -> MiningUnit:
    features = {
        "title.is_how_to": TRUE if how_to else NONE,
        "title.has_year": TRUE if how_to or n == 15 else NONE,
        "title.script": frozenset({"latin"}),
        "title.is_question": TRUE if dominated else NONE,
        "title.has_emoji": TRUE if rare else NONE,
        "segment": frozenset({"long_form"}),
        "topic": frozenset({topic}) if topic else NONE,
    }
    return MiningUnit(
        video_id=video_id(f"m{channel}", n),
        channel_id=channel_id(channel),
        is_outlier=outlier,
        ratio=ratio if ratio is not None else (4.0 if outlier else 1.0),
        published_at=AS_OF - timedelta(days=n % 30, hours=channel),
        features=features,
        review_flagged=frozenset({("topic", topic)}) if flagged and topic else frozenset(),
    )


def population() -> list[MiningUnit]:
    units = []
    for channel in (1, 2, 3):
        for n in range(30):
            how_to = n < 10
            outlier = n < 4 or n == 20
            dominated = channel == 1 and n in (4, 5, 6, 7, 8) or n in (11, 12, 13)
            units.append(
                unit(
                    channel,
                    n,
                    outlier=outlier or (channel == 1 and n in (11, 12, 13)),
                    how_to=how_to,
                    dominated=dominated,
                    rare=n in (0, 25),
                    topic="tr_000000000001" if n < 12 else None,
                    flagged=True,
                )
            )
    return units


def lifts_by_label(units, request=REQUEST, cfg=None):
    lifts = PatternMiner(cfg or PatternSettings()).mine(units, request)
    return {(x.trait_key, x.trait_value, x.interaction): x for x in lifts}


def test_supported_pattern_across_channels():
    lift = lifts_by_label(population())[("title.is_how_to", "true", None)]
    assert lift.status == "supported"
    assert (lift.outliers_with, lift.n_with, lift.outliers_without, lift.n_without) == (12, 30, 6, 60)
    assert lift.lift == (12 / 30) / (6 / 60)
    assert lift.lift_ci[0] > 1 and lift.q_value <= 0.10
    assert lift.channel_balanced_lift >= 1.2
    assert lift.outlier_channels_with == 3
    assert lift.reference_video_ids[0] == "m3000000003"
    assert lift.rate_with_wilson[0] < lift.outlier_rate_with < lift.rate_with_wilson[1]


def test_correlated_twin_trait_is_marked_redundant():
    lifts = lifts_by_label(population())
    twin = lifts[("title.has_year", "true", None)]
    assert twin.status == "redundant"
    assert twin.redundant_with == "title.is_how_to=true"


def test_trait_on_every_video_has_no_contrast():
    assert lifts_by_label(population())[("title.script", "latin", None)].status == "no_contrast"


def test_rare_trait_lacks_support():
    assert lifts_by_label(population())[("title.has_emoji", "true", None)].status == "insufficient_support"


def test_single_channel_driven_trait_is_dominated():
    units = []
    for channel in (1, 2, 3):
        for n in range(30):
            heavy = channel == 1 and n < 8
            units.append(
                unit(
                    channel,
                    n,
                    outlier=heavy or (channel == 2 and n == 0),
                    how_to=False,
                    dominated=heavy or n in (10, 11) or (channel == 2 and n == 0),
                )
            )
    lift = lifts_by_label(units)[("title.is_question", "true", None)]
    assert lift.status == "dominated" and lift.max_channel_share > 0.5


def test_review_flagged_topics_stay_exploratory():
    lift = lifts_by_label(population())[("topic", "tr_000000000001", None)]
    assert lift.status == "exploratory"


def test_single_channel_scope_is_never_supported():
    units = [u for u in population() if u.channel_id == channel_id(1)]
    request = replace(REQUEST, scope=LiftScope.channel, single_channel=True)
    lift = lifts_by_label(units, request)[("title.is_how_to", "true", None)]
    assert lift.status == "exploratory"


def test_interactions_are_pre_registered_and_must_add_over_main_effects():
    lifts = PatternMiner(PatternSettings()).mine(population(), REQUEST)
    interactions = [x for x in lifts if x.interaction is not None]
    assert interactions
    assert {(x.trait_key, x.interaction[0]) for x in interactions} <= {
        (a, b) for a, b in PatternSettings().interaction_pairs
    }
    assert all(x.status != "supported" or x.rate_with_wilson[0] > 0.2 for x in interactions)


def test_ratio_quantiles_are_winsorised():
    units = population()
    units[0] = unit(1, 0, outlier=True, how_to=True, ratio=900.0)
    lift = lifts_by_label(units)[("title.is_how_to", "true", None)]
    assert lift.ratio_quantiles_with[2] <= 50.0


def test_cross_channel_reuploads_count_once():
    units = []
    for channel in (1, 2):
        for n in range(12):
            base = unit(channel, n, outlier=n < 2, how_to=n < 2)
            units.append(replace(base, cross_duplicate_group="xdup_1" if n < 2 else None))
    lift = lifts_by_label(units)[("title.is_how_to", "true", None)]
    assert lift.status == "insufficient_support"


def test_mining_is_order_independent():
    units = population()
    assert PatternMiner(PatternSettings()).mine(units, REQUEST) == PatternMiner(PatternSettings()).mine(
        list(reversed(units)), REQUEST
    )


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.booleans(), st.booleans(), st.integers(1, 4)), min_size=4, max_size=60))
def test_contingency_cells_partition_the_population(rows):
    units = [unit(ch, i, outlier=o, how_to=h) for i, (o, h, ch) in enumerate(rows)]
    table = split(units, lambda u: u.has("title.is_how_to", "true"))
    assert table.a + table.b + table.c + table.d == len(units)
    with_rate, without_rate = table.balanced_rates()
    for rate in (with_rate, without_rate):
        assert rate is None or 0.0 <= rate <= 1.0
    assert 0.0 <= table.max_channel_share() <= 1.0
