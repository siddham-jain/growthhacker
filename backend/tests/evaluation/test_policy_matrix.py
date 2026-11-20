import argparse
import re

import pytest

from app.core.config import REPO_ROOT
from app.evaluation.cli import EvaluationRuntime
from app.evaluation.policy import features  # noqa: F401 - registers the feature rows
from app.evaluation.policy.commands import check, render
from app.evaluation.policy.coverage import (
    CHECKED_MODELS,
    flag_rows_without_notes,
    policy_problems,
    required_fields,
    uncovered_fields,
    unknown_covers,
)
from app.evaluation.policy.matrix import render_policy_matrix
from app.evaluation.policy.registry import GUARDRAILS, FeatureRegistry, FeatureSpec, feature_registry
from tests.conftest import make_settings

MATRIX_DOC = REPO_ROOT / "docs" / "evaluation" / "feature-policy-matrix.md"
SCORE_TERMS = re.compile(r"leaderboard|channel score|creator score|channel rank", re.IGNORECASE)
NEGATIONS = re.compile(r"\b(no|never|not)\b", re.IGNORECASE)


def test_matrix_has_50_contiguous_rows() -> None:
    specs = feature_registry.all()
    assert len(specs) == 50
    assert [spec.number for spec in specs] == list(range(1, 51))
    assert feature_registry.problems() == []


def test_every_row_checks_all_six_guardrails() -> None:
    for spec in feature_registry.all():
        assert tuple(sorted(spec.checks)) == GUARDRAILS, spec.key


def test_flag_rows_have_review_notes_and_match_the_signed_off_set() -> None:
    assert flag_rows_without_notes(feature_registry) == []
    flagged = {spec.number: spec.flagged_guardrails for spec in feature_registry.flagged()}
    assert flagged == {
        4: ["G5"],
        19: ["G4"],
        25: ["G1"],
        26: ["G1", "G4"],
        31: ["G2"],
        32: ["G2"],
        41: ["G2"],
        50: ["G2"],
    }


def test_every_computed_field_has_a_policy_row() -> None:
    assert uncovered_fields(feature_registry) == []
    assert len(CHECKED_MODELS) == 10


def test_covers_name_only_real_fields() -> None:
    assert unknown_covers(feature_registry) == []


def test_scorer_components_and_penalties_are_covered() -> None:
    scorer = [name for name in required_fields() if name.count(".") == 2]
    assert any(name.startswith("ScoreBreakdown.components.") for name in scorer)
    assert any(name.startswith("ScoreBreakdown.penalties.") for name in scorer)
    covered = {name for spec in feature_registry.all() for name in spec.covers}
    assert set(scorer) <= covered


def test_no_row_allows_a_channel_score_or_leaderboard() -> None:
    for spec in feature_registry.all():
        for text in (spec.name, spec.condition, spec.review_note or ""):
            for segment in re.split(r"[;.]", text):
                if SCORE_TERMS.search(segment):
                    assert NEGATIONS.search(segment), f"{spec.key}: {segment.strip()}"


def test_mostpopular_chart_is_not_a_feature() -> None:
    trending = feature_registry.get("analytics.trending")
    assert "not used" in trending.condition
    assert trending.checks["G4"] == "OK"


def test_registry_rejects_inconsistent_specs() -> None:
    base = feature_registry.get("analytics.outlier_ratio").model_dump()
    with pytest.raises(ValueError, match="review note"):
        FeatureSpec(**{**base, "checks": {**base["checks"], "G2": "FLAG"}})
    with pytest.raises(ValueError, match="checks must cover"):
        FeatureSpec(**{**base, "checks": {"G1": "OK"}})
    with pytest.raises(ValueError, match="retention"):
        FeatureSpec(**{**base, "retention_days": None})
    registry = FeatureRegistry()
    registry.register(FeatureSpec(**base))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FeatureSpec(**base))
    with pytest.raises(ValueError, match="taken"):
        registry.register(FeatureSpec(**{**base, "key": "analytics.other"}))
    assert registry.problems()


def test_policy_check_is_clean() -> None:
    assert policy_problems(feature_registry) == []


def test_committed_matrix_matches_the_registry() -> None:
    # regenerate with: uv run python -m app.evaluation policy render
    assert MATRIX_DOC.read_text(encoding="utf-8") == render_policy_matrix(feature_registry)


async def test_cli_render_and_check_need_no_database(tmp_path) -> None:
    runtime = EvaluationRuntime(make_settings())
    out = tmp_path / "matrix.md"
    assert await render(argparse.Namespace(out=str(out)), runtime) == 0
    assert out.read_text(encoding="utf-8") == render_policy_matrix(feature_registry)
    assert await check(argparse.Namespace(), runtime) == 0
    assert runtime._container is None
