"""opt-in live check for Apollo: at most 3 basic tavily searches (3 credits) and one videos.list call (1 unit)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import delete, insert

import app.adapters.youtube.factory  # noqa: F401  (registers the youtube gateway)
from app.adapters.search.tavily_answers import TavilyAnswerSearch
from app.core.config import Settings
from app.core.container import Container, build_container
from app.core.tenancy import TenantScope
from app.models.competitors import CompetitorSet
from app.models.tenancy import Client, Workspace
from app.models.website import BuyerQuestion
from app.models.youtube_cache import YtChannel, YtVideo
from app.services.answer_citations.source import AnswerCitationSource
from app.services.answer_citations.store import AnswerCitationStore
from app.services.answer_citations.task import AnswerCitationRunner
from tests.conftest import make_settings

pytestmark = [pytest.mark.live, pytest.mark.db]

APOLLO = "UC0InVdvqNyNzKBl1-TL348A"
APOLLO_247 = "UCdZXd2_JaNA8EBLd4fpJU3A"
QUESTIONS = (
    "Is Total Hip Replacement safe for patients with hypertension?",
    "Does Apollo offer online video consultations with cardiologists?",
    "How do I book a cardiologist appointment at Apollo?",
)
MAX_CREDITS = 3


@pytest.fixture
async def container(migrated_database: str) -> AsyncIterator[Container]:
    real = Settings()
    if real.tavily.api_key is None or real.youtube.api_key is None:
        pytest.skip("TAVILY_API_KEY and YOUTUBE_API_KEY are needed")
    container = build_container(
        make_settings(
            youtube={"api_key": real.youtube.api_key.get_secret_value()},
            tavily={"api_key": real.tavily.api_key.get_secret_value()},
            answer_citations={"questions_per_week": MAX_CREDITS},
        )
    )
    yield container
    await container.aclose()


@pytest.fixture
async def apollo(container: Container) -> AsyncIterator[Client]:
    started = container.clock.now()
    async with container.session_factory() as session, session.begin():
        workspace_id = (
            await session.execute(
                insert(Workspace)
                .values(name="apollo-live", slug=f"apollo-live-{uuid.uuid4().hex[:6]}")
                .returning(Workspace.id)
            )
        ).scalar_one()
        client = Client(
            workspace_id=workspace_id,
            name="Apollo Hospitals",
            slug="apollo-live",
            own_channel_id=APOLLO,
            own_channel_status="verified",
            country="IN",
            lifecycle="active",
        )
        session.add(client)
        await session.flush()
        session.add(
            CompetitorSet(
                workspace_id=workspace_id,
                client_id=client.id,
                version=1,
                competitor_channel_ids=[APOLLO_247],
                confirmed_by="user",
                confirmed_at=container.clock.now(),
            )
        )
        for text in QUESTIONS:
            await session.execute(insert(BuyerQuestion).values(**_question(workspace_id, client.id, text)))
    yield client
    # the smoke writes real cache rows; remove them so unit tests see a clean shared cache
    async with container.session_factory() as session, session.begin():
        await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await session.execute(delete(YtVideo).where(YtVideo.first_seen_at >= started))
        await session.execute(delete(YtChannel).where(YtChannel.first_seen_at >= started))


def _question(workspace_id: uuid.UUID, client_id: uuid.UUID, text: str) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "client_id": client_id,
        "text": text,
        "normalized_text": text.casefold(),
        "language": "en",
        "funnel_stage": "consideration",
        "intent": "procedure_how",
        "source": "user",
        "status": "confirmed",
        "priority": 1,
    }


async def test_apollo_questions_are_checked_within_the_credit_cap(
    container: Container, apollo: Client
) -> None:
    search: TavilyAnswerSearch = container.adapter("answer_search")
    settings = container.settings.answer_citations
    source = AnswerCitationSource(
        search, container.adapter("youtube"), container.clock, max_results=settings.max_results
    )
    runner = AnswerCitationRunner(source, container.session_factory, container.clock, settings)

    result = await runner.run_client(apollo, credit_budget=MAX_CREDITS)

    assert result.questions == len(QUESTIONS)
    assert search.meter.spent <= MAX_CREDITS
    stored = await AnswerCitationStore(
        container.session_factory, TenantScope(workspace_id=apollo.workspace_id, client_id=apollo.id)
    ).latest(container.clock.now().replace(year=2000))
    assert {check.text for check in stored} == set(QUESTIONS)
    for check in stored:
        assert all(citation.channel_id.startswith("UC") for citation in check.citations)
    print(
        "\nlive answer citations:",
        {check.text[:40]: [(c.video_id, c.channel_id) for c in check.citations] for check in stored},
        f"credits={search.meter.spent}",
    )
