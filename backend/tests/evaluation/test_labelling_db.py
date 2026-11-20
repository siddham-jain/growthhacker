import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.errors import NotFound
from app.api.pagination import PageRequest
from app.api.schemas.admin import LabelIn
from app.core.clock import FrozenClock
from app.core.config import LabellingSettings
from app.evaluation.labelling.api import LabellingApi
from app.evaluation.labelling.models import Population, PopulationVideo
from app.evaluation.labelling.sampler import OutlierSampleDesigner
from app.evaluation.labelling.scorer import evaluate_labels
from app.evaluation.labelling.store import LabelStore
from app.evaluation.labelling.universe import clients_by_channel
from app.evaluation.packs.ids import batch_uuid
from tests.conftest import make_settings
from tests.foundation.factories import create_tenant

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
CHANNEL = "UC" + "a" * 22


def population() -> Population:
    videos = tuple(
        PopulationVideo(
            video_id=f"lab{n:08d}",
            channel_id=CHANNEL if n < 3 else f"UC{n:022d}",
            channel_title="Channel",
            client_ids=("c",),
            title=f"Video {n}",
            segment="long_form",
            duration_s=600,
            published_at=NOW - timedelta(days=10),
            age_days=10.0,
            views=1_000,
            ratio=ratio,
            is_outlier=ratio >= 3.0,
            subscriber_count=50_000,
            hidden_subscribers=False,
            thumbnail_url=None,
            oldest_api_at=NOW - timedelta(days=1),
        )
        for n, ratio in enumerate([7.0, 4.0, 2.0, 0.5, 6.5, 3.3, 2.1, 0.4])
    )
    return Population(observed_at=NOW, threshold=3.0, detector_version="det1", videos=videos, uploads={})


async def test_store_round_trip_and_api(session_factory) -> None:
    settings = make_settings()
    container = SimpleNamespace(session_factory=session_factory, settings=settings, clock=FrozenClock(NOW))
    async with session_factory() as session, session.begin():
        scope = await create_tenant(session, "labels", own_channel_id=CHANNEL)
    pop = population()
    design = OutlierSampleDesigner(LabellingSettings(max_per_channel=5)).design(
        pop, seed=1, batch_id=batch_uuid(NOW)
    )
    async with session_factory() as session, session.begin():
        store = LabelStore(session)
        assert await store.save_design(design, pop, retention_days=28) == len(design.tasks)
        assert await store.latest_batch_id() == design.batch_id
        tasks = await store.tasks(design.batch_id)
    assert {t.video_id for t in tasks} == {v.video_id for v in pop.videos}

    api = LabellingApi(container)
    page = await api.labels(scope, None, PageRequest(limit=2))
    assert page.total == 3
    assert len(page.items) == 2 and page.next_cursor == "o:2"
    saved = await api.put_label(scope, "lab00000000", LabelIn(is_outlier=True, labeller="Mira", note="clear"))
    assert saved.is_outlier is True and saved.client_id == str(scope.client_id)
    with pytest.raises(NotFound):
        await api.put_label(scope, "lab00000004", LabelIn(is_outlier=True, labeller="Mira"))

    async with session_factory() as session, session.begin():
        store = LabelStore(session)
        labels = await store.labels([t.task_id for t in tasks])
        result, gold = evaluate_labels(
            design.batch_id,
            tasks,
            labels,
            LabellingSettings(bootstrap_resamples=100),
            threshold=3.0,
            seed=1,
            clients_by_channel=await clients_by_channel(session),
        )
        assert await store.save_gold(list(gold.values())) == 1
        await store.save_result(result)
    precision = await api.precision(scope, None)
    assert precision.n == 1 and precision.precision == 1.0
    per_client = await api.precision(scope, scope.client_id)
    assert per_client.n == 1
    other = await api.precision(scope, uuid.uuid4())
    assert other.n == 0 and other.precision is None
