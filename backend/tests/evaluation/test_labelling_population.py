from app.core.config import LabellingSettings
from app.evaluation.labelling.population import UPLOADS_SHOWN, build_population
from app.evaluation.labelling.sampler import OutlierSampleDesigner
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.settings import AnalyticsEngineSettings
from tests.analytics.factories import AS_OF, CLIENT_ID
from tests.analytics.test_golden import build_inputs


def test_population_uses_the_detector_at_the_comparison_age() -> None:
    settings = AnalyticsEngineSettings()
    inputs = build_inputs()
    result = AnalyticsEngine(settings).run(inputs, AS_OF)
    population = build_population([(str(CLIENT_ID), inputs, result)], settings, 30, AS_OF)
    age = settings.comparison_age(30)
    assessed = {vid for vid, m in result.metrics[age].items() if m.outlier is not None}
    assert {v.video_id for v in population.videos} == assessed
    for video in population.videos:
        metric = result.metrics[age][video.video_id]
        assert video.ratio == metric.outlier.ratio
        assert video.is_outlier == metric.outlier.is_outlier
        assert video.client_ids == (str(CLIENT_ID),)
    assert population.threshold == settings.outliers.outlier_ratio
    assert all(len(points) <= UPLOADS_SHOWN for points in population.uploads.values())
    design = OutlierSampleDesigner(LabellingSettings()).design(population, seed=5)
    assert design.tasks
    assert all(0 < t.inclusion_prob <= 1 for t in design.tasks)
