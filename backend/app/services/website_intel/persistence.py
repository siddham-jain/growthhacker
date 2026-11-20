from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile, BuyerQuestion, ProfileDiff, ReviewStatus
from app.domain.web import SiteRef, SourceKind
from app.models.website import (
    BusinessProfile as ProfileRow,
)
from app.models.website import (
    BuyerQuestion as QuestionRow,
)
from app.models.website import (
    CrawledPage as PageRow,
)
from app.models.website import (
    CrawlRun as CrawlRunRow,
)
from app.models.website import (
    PageFacts as PageFactsRow,
)
from app.models.website import (
    ProfileDiff as DiffRow,
)
from app.models.website import (
    ProfileTombstone as TombstoneRow,
)
from app.models.website import (
    UrlInventoryEntry,
    Website,
)
from app.repositories.tenant.base import TenantRepository
from app.services.website_intel.acquisition.cascade import CascadeOutcome
from app.services.website_intel.inputs.urls import url_hash
from app.services.website_intel.models import ExtractedPage
from app.services.website_intel.profile.merger import Tombstone

FETCH_MODES = {
    SourceKind.site_direct: "direct",
    SourceKind.site_rendered: "render",
    SourceKind.reader_proxy: "reader",
    SourceKind.search_index: "tavily",
    SourceKind.archive: "archive",
    SourceKind.knowledge_base: "knowledge_base",
    SourceKind.web_mentions: "tavily",
    SourceKind.user: "user",
}
# channel snippets are youtube api data under the 28-day rule, so they are never stored as pages
UNSTORED_SOURCES = frozenset({SourceKind.youtube_channel})
MAX_STORED_INVENTORY = 5_000


class ProfileRepository(TenantRepository[ProfileRow]):
    model = ProfileRow

    async def latest(self) -> ProfileRow | None:
        statement = self._scoped(select(ProfileRow)).order_by(ProfileRow.version.desc()).limit(1)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def max_version(self) -> int:
        return (
            await self.session.execute(self._scoped(select(func.max(ProfileRow.version))))
        ).scalar_one_or_none() or 0


class BuyerQuestionRepository(TenantRepository[QuestionRow]):
    model = QuestionRow

    async def all_rows(self) -> Sequence[QuestionRow]:
        return (await self.session.execute(self._scoped(select(QuestionRow)))).scalars().all()


class TombstoneRepository(TenantRepository[TombstoneRow]):
    model = TombstoneRow

    async def all_rows(self) -> Sequence[TombstoneRow]:
        return (await self.session.execute(self._scoped(select(TombstoneRow)))).scalars().all()


class PageFactsRepository(TenantRepository[PageFactsRow]):
    model = PageFactsRow

    async def cached(
        self, content_hash: str, extractor_version: str, prompt_version: str
    ) -> list[Any] | None:
        statement = self._scoped(
            select(PageFactsRow.facts).where(
                PageFactsRow.content_hash == content_hash,
                PageFactsRow.extractor_version == extractor_version,
                PageFactsRow.prompt_version == prompt_version,
                PageFactsRow.superseded_at.is_(None),
            )
        ).limit(1)
        return (await self.session.execute(statement)).scalar_one_or_none()


class WebsiteIntelStore:
    """tenant-scoped persistence for websites, crawls, pages, map cache, profiles and buyer questions."""

    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        if scope.client_id is None:
            raise ValueError("website intel persistence needs a client-bound scope")
        self.session = session
        self.scope = scope
        self._tenant = {"workspace_id": scope.workspace_id, "client_id": scope.client_id}
        self._profiles = ProfileRepository(session, scope)
        self._questions = BuyerQuestionRepository(session, scope)
        self._tombstones = TombstoneRepository(session, scope)

    async def upsert_website(
        self, site: SiteRef, *, scope_mode: str, ownership_attested: bool, related: list[dict[str, Any]]
    ) -> UUID:
        values = {
            **self._tenant,
            "input_url": site.input_url,
            "canonical_origin": site.canonical_origin,
            "registrable_domain": site.registrable_domain,
            "scope_hosts": site.scope_hosts,
            "scope_mode": scope_mode,
            "focus_path": site.focus_path,
            "related_brands": related,
            "ownership_attested": ownership_attested,
        }
        statement = insert(Website).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["client_id", "registrable_domain", "focus_path"],
            set_={
                key: statement.excluded[key]
                for key in ("canonical_origin", "scope_hosts", "related_brands", "input_url")
            },
        ).returning(Website.id)
        return (await self.session.execute(statement)).scalar_one()

    async def record_crawl(
        self,
        website_id: UUID,
        *,
        run_id: UUID | None,
        trigger: str,
        profile: str,
        outcome: CascadeOutcome,
        started_at: datetime,
        finished_at: datetime,
    ) -> UUID:
        site_pages = [page for page in outcome.pages if page.page.source_kind is SourceKind.site_direct]
        if not outcome.pages:
            status = "blocked" if outcome.blocked else "failed"
        else:
            status = "completed" if site_pages and not outcome.blocked else "partial"
        stats = {
            **dict(outcome.stats),
            "pages": len(outcome.pages),
            "tavily_credits": round(outcome.tavily_credits, 2),
            "by_source": {kind.value: count for kind, count in outcome.sources_used.items()},
            "tiers": [
                {
                    "tier": tier.tier,
                    "source": tier.source_kind.value,
                    "pages": tier.pages,
                    "blocked": tier.blocked,
                }
                for tier in outcome.tiers
            ],
        }
        statement = (
            insert(CrawlRunRow)
            .values(
                **self._tenant,
                website_id=website_id,
                run_id=run_id,
                trigger=trigger,
                profile=profile,
                status=status,
                ua_mode=outcome.ua_mode.value,
                stats=stats,
                started_at=started_at,
                finished_at=finished_at,
            )
            .returning(CrawlRunRow.id)
        )
        crawl_run_id = (await self.session.execute(statement)).scalar_one()
        await self.session.execute(
            update(Website).where(Website.id == website_id).values(last_crawl_run_id=crawl_run_id)
        )
        return crawl_run_id

    async def upsert_pages(self, website_id: UUID, crawl_run_id: UUID, pages: Sequence[ExtractedPage]) -> int:
        stored = 0
        for extracted in pages:
            page, details = extracted.page, extracted.details
            if page.source_kind in UNSTORED_SOURCES:
                continue
            values = {
                **self._tenant,
                "id": page.page_id,
                "website_id": website_id,
                "url_hash": url_hash(page.canonical_url),
                "url": page.url,
                "canonical_url": page.canonical_url,
                "final_url": page.final_url,
                "status_code": page.status_code,
                "outcome": "ok",
                "content_type": page.content_type,
                "lang": page.lang,
                "title": page.title,
                "meta_description": page.meta_description,
                "h1": page.h1,
                "headings": page.headings,
                "text": page.text,
                "content_hash": page.content_hash,
                "simhash": details.simhash - (1 << 64)
                if details.simhash and details.simhash >= 1 << 63
                else details.simhash,
                "json_ld": page.json_ld,
                "ctas": [cta.model_dump(mode="json") for cta in details.ctas],
                "prices": [price.model_dump(mode="json") for price in details.prices],
                "links_internal": page.links_internal,
                "youtube_refs": page.youtube_refs,
                "page_type": page.page_type,
                "template_key": page.template_key,
                "fetch_mode": FETCH_MODES.get(page.source_kind, "direct"),
                "source_kind": page.source_kind.value,
                "source_url": page.url,
                "snapshot_at": page.snapshot_at,
                "injection_flags": page.injection_flags,
                "truncated": page.truncated,
                "low_trust": page.low_trust,
                "fetched_at": page.fetched_at,
                "last_crawl_run_id": crawl_run_id,
            }
            statement = insert(PageRow).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["website_id", "url_hash"],
                set_={
                    key: statement.excluded[key]
                    for key in values
                    if key not in {"id", "website_id", "url_hash", "workspace_id", "client_id"}
                },
            )
            await self.session.execute(statement)
            stored += 1
        return stored

    async def upsert_inventory(self, website_id: UUID, outcome: CascadeOutcome, now: datetime) -> None:
        rows = [
            {
                **self._tenant,
                "website_id": website_id,
                "url_hash": url_hash(item.url),
                "url": item.url,
                "template_key": item.template_key,
                "page_type_guess": item.page_type.value,
                "first_seen": now,
                "last_seen": now,
                "sitemap_lastmod": item.lastmod,
            }
            for item in outcome.inventory[:MAX_STORED_INVENTORY]
        ]
        for start in range(0, len(rows), 500):
            statement = insert(UrlInventoryEntry).values(rows[start : start + 500])
            statement = statement.on_conflict_do_update(
                index_elements=["website_id", "url_hash"],
                set_={
                    "last_seen": statement.excluded.last_seen,
                    "sitemap_lastmod": statement.excluded.sitemap_lastmod,
                },
            )
            await self.session.execute(statement)

    async def latest_profile(self) -> BusinessProfile | None:
        row = await self._profiles.latest()
        return BusinessProfile.model_validate(row.profile) if row is not None else None

    async def next_version(self) -> int:
        return await self._profiles.max_version() + 1

    async def save_profile(
        self,
        profile: BusinessProfile,
        *,
        created_by: str,
        llm_usage: dict[str, Any],
        prompt_versions: dict[str, str],
    ) -> UUID:
        await self.session.execute(
            update(ProfileRow)
            .where(
                ProfileRow.workspace_id == self.scope.workspace_id,
                ProfileRow.client_id == self.scope.client_id,
                ProfileRow.status.in_(("draft", "awaiting_confirmation")),
            )
            .values(status="superseded")
        )
        statement = (
            insert(ProfileRow)
            .values(
                **self._tenant,
                id=profile.profile_id,
                version=profile.version,
                parent_version=profile.parent_version,
                crawl_run_id=profile.crawl_run_id,
                status=profile.status,
                created_by=created_by,
                profile=profile.model_dump(mode="json"),
                coverage=profile.coverage.model_dump(mode="json"),
                quality=profile.quality.model_dump(mode="json"),
                llm_usage=llm_usage,
                prompt_versions=prompt_versions,
                generated_at=profile.generated_at,
            )
            .returning(ProfileRow.id)
        )
        return (await self.session.execute(statement)).scalar_one()

    async def save_diff(self, diff: ProfileDiff) -> None:
        self.session.add(
            DiffRow(
                **self._tenant,
                from_version=diff.from_version or None,
                to_version=diff.to_version,
                diff=diff.model_dump(mode="json"),
                significance=diff.significance,
            )
        )

    async def tombstones(self) -> list[Tombstone]:
        rows = await self._tombstones.all_rows()
        return [Tombstone(row.collection, row.item_key, row.text) for row in rows]

    async def add_tombstones(self, stones: Sequence[Tombstone]) -> None:
        for stone in stones:
            statement = insert(TombstoneRow).values(
                **self._tenant, collection=stone.collection, item_key=stone.item_key, text=stone.text
            )
            await self.session.execute(statement.on_conflict_do_nothing())

    async def buyer_questions(self) -> list[BuyerQuestion]:
        rows = await self._questions.all_rows()
        return [self._question(row) for row in rows]

    @staticmethod
    def _question(row: QuestionRow) -> BuyerQuestion:
        return BuyerQuestion(
            id=row.id,
            client_id=row.client_id,
            text=row.text,
            normalized_text=row.normalized_text,
            language=row.language,
            funnel_stage=row.funnel_stage,  # type: ignore[arg-type]
            intent=row.intent,  # type: ignore[arg-type]
            offering_ids=list(row.offering_ids),
            conversion_action_id=row.conversion_action_id,
            geo_modifier=row.geo_modifier,
            source=row.source,  # type: ignore[arg-type]
            source_ref=row.source_ref,
            status=ReviewStatus(row.status),
            priority=row.priority,  # type: ignore[arg-type]
            ai_cited_video_ids=row.ai_cited_video_ids,
            ai_citations_observed_at=row.ai_citations_observed_at,
            meta=row.meta,  # type: ignore[arg-type]
        )

    async def save_questions(self, questions: Sequence[BuyerQuestion]) -> None:
        for question in questions:
            values = {
                **self._tenant,
                "id": question.id,
                "text": question.text,
                "normalized_text": question.normalized_text,
                "language": question.language,
                "funnel_stage": question.funnel_stage.value,
                "intent": question.intent.value,
                "offering_ids": question.offering_ids,
                "conversion_action_id": question.conversion_action_id,
                "geo_modifier": question.geo_modifier,
                "source": question.source.value,
                "source_ref": question.source_ref,
                "status": question.status.value,
                "priority": question.priority,
                "ai_cited_video_ids": question.ai_cited_video_ids,
                "meta": question.meta.model_dump(mode="json"),
            }
            statement = insert(QuestionRow).values(**values)
            # user decisions survive re-crawls: only system fields are refreshed on conflict
            statement = statement.on_conflict_do_update(
                index_elements=["client_id", "normalized_text", "language"],
                set_={
                    "offering_ids": statement.excluded.offering_ids,
                    "funnel_stage": statement.excluded.funnel_stage,
                    "intent": statement.excluded.intent,
                    "priority": statement.excluded.priority,
                    "updated_at": func.now(),
                },
            )
            await self.session.execute(statement)


class PgMapCache:
    """page_facts rows keyed by content hash, extractor and prompt version; unchanged pages cost no llm."""

    def __init__(self, session_factory: SessionFactory, scope: TenantScope) -> None:
        self._session_factory = session_factory
        self._scope = scope

    async def get(
        self, content_hash: str, extractor_version: str, prompt_version: str
    ) -> list[dict[str, Any]] | None:
        async with self._session_factory() as session:
            return await PageFactsRepository(session, self._scope).cached(
                content_hash, extractor_version, prompt_version
            )

    async def put(
        self,
        page: ExtractedPage,
        *,
        extractor_version: str,
        prompt_version: str,
        model: str,
        facts: list[dict[str, Any]],
        verified: int,
        dropped: int,
    ) -> None:
        if page.page.source_kind in UNSTORED_SOURCES:
            return
        async with self._session_factory() as session:
            exists = await session.scalar(select(PageRow.id).where(PageRow.id == page.page.page_id))
            if exists is None:
                return
            statement = insert(PageFactsRow).values(
                workspace_id=self._scope.workspace_id,
                client_id=self._scope.client_id,
                page_id=page.page.page_id,
                content_hash=page.page.content_hash,
                extractor_version=extractor_version,
                prompt_version=prompt_version,
                model=model,
                facts=facts,
                verified_count=verified,
                dropped_count=dropped,
                source_kind=page.page.source_kind.value,
                source_url=page.page.url,
                snapshot_at=page.page.snapshot_at,
                fetched_at=page.page.fetched_at,
            )
            await session.execute(statement.on_conflict_do_nothing())
            await session.commit()
