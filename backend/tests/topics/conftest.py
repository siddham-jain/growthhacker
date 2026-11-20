from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import insert

from app.core.config import TopicEngineSettings
from app.core.db import UnitOfWork
from app.models.youtube_cache import YtChannel, YtVideo
from app.services.topics.lexicon import Lexicons, load_lexicons
from tests.foundation.factories import create_tenant
from tests.topics.fixtures import CHANNEL, NOW, FrozenEmbedder, video_text

FROZEN = Path(__file__).parent / "golden" / "embeddings.npz"


@pytest.fixture
def settings() -> TopicEngineSettings:
    return TopicEngineSettings()


@pytest.fixture(scope="session")
def lexicons() -> Lexicons:
    return load_lexicons()


@pytest.fixture(scope="session")
def frozen_embedder() -> FrozenEmbedder:
    data = np.load(FROZEN)
    vectors = data["vectors"].astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return FrozenEmbedder(
        dict(zip(data["keys"].tolist(), vectors, strict=True)), str(data["model_id"]), vectors.shape[1]
    )


DB_VIDEOS = [
    ("Knee Replacement Cost in India | Dr Rao explains", "What does a total knee replacement cost?"),
    ("घुटने का प्रत्यारोपण कितने का होता है?", ""),
    ("Ghutne ka operation kab karwana chahiye", ""),
    ("Life after cardiac bypass surgery: recovery timeline", "Recovery takes 6 weeks."),
    ("Apollo 11 moon landing documentary", "The 1969 mission."),
]


@pytest.fixture
async def scope(session):
    tenant = await create_tenant(session, "apollo-topics")
    await session.commit()
    return tenant


@pytest.fixture
def uow_factory(session_factory):
    return lambda read_only=False: UnitOfWork(session_factory, read_only=read_only)


@pytest.fixture
async def video_ids(session) -> list[str]:
    await session.execute(
        insert(YtChannel).values(
            channel_id=CHANNEL, title="Apollo", metadata_fetched_at=NOW, stats_fetched_at=NOW
        )
    )
    ids = []
    for index, (title, description) in enumerate(DB_VIDEOS):
        text = video_text(title, description)
        ids.append(text.video_id)
        await session.execute(
            insert(YtVideo).values(
                video_id=text.video_id,
                channel_id=CHANNEL,
                title=title,
                description=description,
                tags=[],
                content_hash=text.content_hash,
                published_at=NOW - timedelta(days=index + 1),
                effective_published_at=NOW - timedelta(days=index + 1),
                ingest_source="uploads",
                metadata_fetched_at=NOW,
                stats_fetched_at=NOW,
            )
        )
    await session.commit()
    return ids
