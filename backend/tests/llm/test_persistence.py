from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.core.clock import FrozenClock
from app.models.ledgers import ProviderUsageLedger
from app.models.runs import PipelineRun
from app.services.llm.result_cache import MAX_TTL_DAYS, SqlResultCache, cache_key
from app.services.llm.usage import AttemptRecord, SqlUsageRecorder
from app.services.llm.writer.copy_store import RecommendationCopyStore
from app.services.llm.writer.evidence_pack import EvidencePackBuilder
from app.services.llm.writer.schemas import WrittenCard
from app.services.llm.writer.template import TemplateCardWriter
from tests.foundation.factories import create_tenant, insert_row
from tests.llm.factories import NOW, make_client_context, make_draft


async def test_sql_result_cache_round_trip_and_expiry(session_factory) -> None:
    clock = FrozenClock(NOW)
    cache = SqlResultCache(session_factory, clock)
    key = cache_key("card_write", "v1+abc", '{"x":1}', "en", "CardCopyLLM")
    await cache.put(key, task="card_write", prompt_version="v1+abc", output={"a": 1}, model="m", ttl_days=7)
    hit = await cache.get(key)
    assert hit is not None and hit.output == {"a": 1} and hit.model == "m"
    await cache.put(key, task="card_write", prompt_version="v1+abc", output={"a": 2}, model="m2", ttl_days=90)
    hit = await cache.get(key)
    assert hit is not None and hit.output == {"a": 2}
    clock.advance(timedelta(days=MAX_TTL_DAYS, seconds=1))
    assert await cache.get(key) is None


async def test_cache_key_depends_on_prompt_version_and_language() -> None:
    base = cache_key("t", "v1+a", "{}", "en", "S")
    assert base != cache_key("t", "v2+b", "{}", "en", "S")
    assert base != cache_key("t", "v1+a", "{}", "hi", "S")


async def test_usage_rows_land_in_the_unified_ledger(session_factory) -> None:
    record = AttemptRecord(
        occurred_at=NOW,
        provider="anthropic",
        operation="card_write",
        stage="write_cards",
        tier="medium",
        prompt_id="card_write",
        prompt_version="v1+abc",
        schema_name="CardCopyLLM",
        model_requested="claude-sonnet-5-5",
        model_resolved="claude-sonnet-5-5",
        route_index=0,
        attempt_no=1,
        attempt_kind="initial",
        outcome="ok",
        error_kind=None,
        status_code=None,
        stop_reason="end_turn",
        refusal_category=None,
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=0,
        cache_write_tokens=0,
        thinking_tokens=0,
        cost_usd=0.0004,
        latency_ms=900,
        provider_request_id="req_1",
        input_hash="a" * 64,
        output_hash="b" * 64,
        run_id=None,
        client_id=None,
    )
    unpriced = replace(record, cost_usd=None, model_resolved="gw-x", request_id=uuid4())
    await SqlUsageRecorder(session_factory).record([record, unpriced])
    async with session_factory() as session:
        rows = (await session.scalars(select(ProviderUsageLedger).order_by(ProviderUsageLedger.id))).all()
    assert [(row.provider_kind, row.scope, row.cost_usd, row.price_missing) for row in rows[-2:]] == [
        ("llm", "shared", Decimal("0.000400"), False),
        ("llm", "shared", None, True),
    ]


async def test_copy_store_upserts_provenance(session_factory) -> None:
    async with session_factory() as session:
        scope = await create_tenant(session, "smile")
        run = await insert_row(session, PipelineRun.__table__, scope)
        await session.commit()
    draft = make_draft()
    built = EvidencePackBuilder().build(draft, make_client_context())
    copy = TemplateCardWriter().write(built, degrade_reason="budget")
    card = WrittenCard(draft_id=draft.draft_id, stable_key=draft.stable_key, card_copy=copy)
    store = RecommendationCopyStore(session_factory)
    await store.save(scope, run["id"], [card])
    await store.save(scope, run["id"], [card])
    row = await store.get(scope, draft.draft_id)
    assert row is not None
    assert row.copy_source == "template" and row.degrade_reason == "budget"
    assert row.reason_fact_ids == [["r_share"], ["r_ratio"], ["r_views"]]
