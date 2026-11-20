"""deterministic replay of a frozen bundle at T: 06's engine, the pre-T window corpus and the frozen baselines."""

import hashlib
import json
from dataclasses import dataclass

from app.core.config import Settings
from app.evaluation.backtest.corpus import point_in_time, window_corpus
from app.evaluation.backtest.models import CorpusVideo
from app.evaluation.backtest.outcomes import FrozenBaselines
from app.evaluation.freeze.bundle import FreezeBundle
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.inputs import AnalyticsInputs
from app.services.analytics.result import AnalyticsResult
from app.services.analytics.settings import AnalyticsEngineSettings


@dataclass(frozen=True)
class FrozenView:
    result: AnalyticsResult
    window: list[CorpusVideo]
    baselines: FrozenBaselines

    def fingerprint(self) -> str:
        payload = {
            "analytics": self.result.content_hash(),
            "window": [video.model_dump(mode="json") for video in self.window],
            "baselines": self.baselines.to_json(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def engine_for(settings: Settings) -> AnalyticsEngine:
    return AnalyticsEngine(AnalyticsEngineSettings.from_settings(settings))


def outlier_threshold(settings: Settings) -> float:
    return AnalyticsEngineSettings.from_settings(settings).outliers.outlier_ratio


def frozen_view(
    engine: AnalyticsEngine, inputs: AnalyticsInputs, bundle: FreezeBundle, window_days: int, age_days: int
) -> FrozenView:
    as_of = bundle.manifest.as_of
    visible = point_in_time(inputs, as_of)
    result = engine.run(visible, as_of)
    return FrozenView(
        result=result,
        window=window_corpus(visible, result, as_of, window_days),
        baselines=FrozenBaselines.from_result(result, visible, age_days),
    )
