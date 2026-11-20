import json
import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.domain.recommendations import (
    Recommendation,
    RecommendationDraft,
    RecommendationSet,
    StrictRecommendation,
)
from app.domain.runs import RunEvent

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
UPDATE_ENV = "GH_UPDATE_CONTRACT_SNAPSHOTS"

SNAPSHOTTED: dict[str, type[BaseModel]] = {
    "recommendation": Recommendation,
    "strict_recommendation": StrictRecommendation,
    "recommendation_draft": RecommendationDraft,
    "recommendation_set": RecommendationSet,
    "run_event": RunEvent,
}


def _render(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(mode="serialization"), indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize("name", sorted(SNAPSHOTTED))
def test_json_schema_matches_snapshot(name: str):
    path = SNAPSHOT_DIR / f"{name}.schema.json"
    rendered = _render(SNAPSHOTTED[name])
    if os.environ.get(UPDATE_ENV) == "1":
        path.write_text(rendered)
    assert path.exists(), f"missing snapshot {path.name}; run with {UPDATE_ENV}=1 to create it"
    assert rendered == path.read_text(), (
        f"{name} schema drifted; contract changes need orchestrator sign-off, then {UPDATE_ENV}=1"
    )
