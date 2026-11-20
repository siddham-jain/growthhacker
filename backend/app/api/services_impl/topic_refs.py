from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ids
from app.api.errors import AppError, NotFound
from app.api.pagination import Page, PageRequest
from app.api.schemas.setup import (
    BuyerQuestionCreate,
    BuyerQuestionItem,
    KeywordCreate,
    KeywordItem,
    TopicRefPatch,
)
from app.api.services import TOPIC_REFS, TopicRefKind, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.api.services_impl.profile import LoadedProfile, ProfileEdits, ProfileMissing, invalid
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile, ClaimMeta, ClaimOrigin, ClaimStatus, NicheKeyword
from app.models.website import BuyerQuestion as QuestionRow
from app.models.website import Keyword as KeywordRow
from app.models.website import ProfileTombstone
from app.services.website_intel.persistence import BuyerQuestionRepository
from app.services.website_intel.profile.claims import normalise_name, stable_id
from app.services.website_intel.profile.merger import EditOp
from app.services.website_intel.questions.service import classify_intent, normalise_question

KEYWORDS = "niche_keywords"
CLAIM_STATUS = {
    ClaimStatus.confirmed: "confirmed",
    ClaimStatus.edited: "confirmed",
    ClaimStatus.rejected: "rejected",
}
STATUS_ORDER = {"confirmed": 0, "proposed": 1, "rejected": 2, "retired": 3}


class TopicRefExists(AppError):
    status = 409
    code = "topic_ref_exists"
    title = "This keyword or question already exists"


class KeywordBook:
    """keywords live in the profile (03's niche_keywords); edits go through profile versions and tombstones."""

    def __init__(self, edits: ProfileEdits) -> None:
        self.edits = edits

    @staticmethod
    def item(keyword: NicheKeyword) -> KeywordItem:
        return KeywordItem(
            id=keyword.id,
            text=keyword.term[:80],
            status=CLAIM_STATUS.get(keyword.meta.status, "proposed"),  # type: ignore[arg-type]
            source=keyword.source,
            language=keyword.language,
            kind=keyword.kind,
            weight=keyword.weight,
            updated_at=keyword.meta.updated_at,
        )

    def items(self, loaded: LoadedProfile) -> list[KeywordItem]:
        live = [self.item(keyword) for keyword in loaded.effective.niche_keywords]
        rejected = [
            KeywordItem(id=stone.item_key, text=stone.text[:80], status="rejected", source="user")
            for stone in loaded.tombstones
            if stone.collection == KEYWORDS
        ]
        return sorted(live + rejected, key=lambda item: (STATUS_ORDER[item.status], item.text.casefold()))

    @staticmethod
    def find(loaded: LoadedProfile, keyword_id: str) -> NicheKeyword | None:
        return next(
            (keyword for keyword in loaded.effective.niche_keywords if keyword.id == keyword_id), None
        )

    def ops(self, loaded: LoadedProfile, keyword_id: str, patch: TopicRefPatch) -> list[EditOp]:
        if patch.priority is not None:
            raise invalid("keywords carry a weight, not a priority", "priority")
        keyword = self.find(loaded, keyword_id)
        stone = self._stone(loaded, keyword_id)
        if keyword is None and stone is None:
            raise NotFound("keyword not found")
        if patch.status in ("proposed", "retired"):
            raise invalid("a keyword can be confirmed or rejected", "status")
        if keyword is None:
            if patch.status != "confirmed":
                raise invalid("a rejected keyword can only be confirmed again", "status")
            assert stone is not None
            return [self._add_op(loaded.effective, patch.text or stone.text, "en", patch.weight or 1.0)]
        if patch.status == "rejected":
            return [EditOp(op="reject", collection=KEYWORDS, item_id=keyword_id)]
        fields: dict[str, Any] = {}
        if patch.text is not None:
            fields["term"] = patch.text.strip()
        if patch.weight is not None:
            fields["weight"] = patch.weight
        if fields:
            return [EditOp(op="edit", collection=KEYWORDS, item_id=keyword_id, fields=fields)]
        if patch.status == "confirmed":
            return [EditOp(op="confirm", collection=KEYWORDS, item_id=keyword_id)]
        raise invalid("send a text, weight or status change", "body")

    def create_op(self, loaded: LoadedProfile, body: KeywordCreate) -> EditOp:
        keyword_id = stable_id("kw", body.text, body.language)
        if self.find(loaded, keyword_id) is not None:
            raise TopicRefExists("this keyword is already in the list", extra={"id": keyword_id})
        return self._add_op(loaded.effective, body.text, body.language, body.weight)

    @staticmethod
    def _add_op(profile: BusinessProfile, text: str, language: str, weight: float) -> EditOp:
        item = {
            "id": stable_id("kw", text, language),
            "term": text.strip(),
            "language": language,
            "kind": "category",
            "tier": "seed",
            "source": "user",
            "weight": weight,
        }
        return EditOp(op="add", collection=KEYWORDS, item=item)

    @staticmethod
    def _stone(loaded: LoadedProfile, keyword_id: str):
        return next(
            (
                stone
                for stone in loaded.tombstones
                if stone.collection == KEYWORDS and stone.item_key == keyword_id
            ),
            None,
        )

    @staticmethod
    async def clear_tombstones(session: AsyncSession, scope: TenantScope, ops: list[EditOp]) -> None:
        # re-adding a rejected keyword lifts its tombstone so the merger stops filtering it
        added = [op.item for op in ops if op.op == "add" and op.item]
        if added:
            keys = [str(item["id"]) for item in added]
            terms = [str(item["term"]) for item in added]
            await session.execute(
                delete(ProfileTombstone).where(
                    ProfileTombstone.workspace_id == scope.workspace_id,
                    ProfileTombstone.client_id == scope.client_id,
                    ProfileTombstone.collection == KEYWORDS,
                    ProfileTombstone.item_key.in_(keys) | ProfileTombstone.text.in_(terms),
                )
            )

    @staticmethod
    async def mirror(
        session: AsyncSession,
        scope: TenantScope,
        before: BusinessProfile,
        after: BusinessProfile,
        ops: list[EditOp],
    ) -> None:
        """keeps 03's keywords table rows in step with the profile, which stays the source 05 reads (99 c40)."""
        terms = {keyword.id: keyword for keyword in [*before.niche_keywords, *after.niche_keywords]}
        current = {keyword.id: keyword for keyword in after.niche_keywords}
        for op in ops:
            key = str(op.item["id"]) if op.op == "add" and op.item else str(op.item_id)
            keyword = current.get(key) or terms.get(key)
            if keyword is None:
                continue
            values = {
                "workspace_id": scope.workspace_id,
                "client_id": scope.client_id,
                "text": keyword.term,
                "normalized_text": normalise_name(keyword.term),
                "language": keyword.language,
                "script": keyword.script,
                "kind": keyword.kind,
                "tier": keyword.tier,
                "source": keyword.source,
                "status": "rejected" if op.op == "reject" else "confirmed",
                "weight": keyword.weight,
                "offering_ids": keyword.offering_ids,
                "meta": {"keyword_id": keyword.id, "claim": keyword.meta.model_dump(mode="json")},
            }
            statement = insert(KeywordRow).values(**values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["client_id", "normalized_text", "language"],
                    set_={name: statement.excluded[name] for name in ("text", "status", "weight", "meta")},
                )
            )


def question_item(row: QuestionRow) -> BuyerQuestionItem:
    return BuyerQuestionItem(
        id=ids.encode(ids.BUYER_QUESTION, row.id),
        text=row.text,
        status=row.status,  # type: ignore[arg-type]
        source=row.source,  # type: ignore[arg-type]
        funnel_stage=row.funnel_stage,  # type: ignore[arg-type]
        intent=row.intent,
        cited_video_ids=list(row.ai_cited_video_ids or []),
        priority=row.priority,
        created_at=row.created_at,
    )


class TopicRefReadService(ServiceBase):
    """keywords and buyer questions, the client's own topic anchors; every change rebuilds 05's universe."""

    def __init__(self, container: Container) -> None:
        super().__init__(container)
        self.edits = ProfileEdits(container)
        self.keywords = KeywordBook(self.edits)

    async def list_items(
        self, scope: TenantScope, kind: TopicRefKind, status: str | None, page: PageRequest
    ) -> Page[KeywordItem] | Page[BuyerQuestionItem]:
        filters = {"kind": kind, "status": status, "client": str(scope.client_id)}
        async with self.session_factory() as session:
            if kind == "keyword":
                items: list[Any] = self.keywords.items(await self.edits.load(session, scope))
            else:
                rows = await BuyerQuestionRepository(session, scope).all_rows()
                items = sorted(
                    (question_item(row) for row in rows),
                    key=lambda item: (STATUS_ORDER[item.status], item.priority, item.text.casefold()),
                )
        if status is not None:
            items = [item for item in items if item.status == status]
        return self.paginate(items, page, filters)

    async def create(
        self, scope: TenantScope, kind: TopicRefKind, body: KeywordCreate | BuyerQuestionCreate
    ) -> KeywordItem | BuyerQuestionItem:
        if kind == "keyword":
            assert isinstance(body, KeywordCreate)
            item_id = await self._edit_keywords(scope, lambda loaded: [self.keywords.create_op(loaded, body)])
            return await self._keyword(scope, item_id[0])
        assert isinstance(body, BuyerQuestionCreate)
        question = await self._create_question(scope, body)
        await self._rebuild(scope)
        return question

    async def update(
        self, scope: TenantScope, kind: TopicRefKind, item_id: str, patch: TopicRefPatch
    ) -> KeywordItem | BuyerQuestionItem:
        if kind == "keyword":
            changed = await self._edit_keywords(
                scope, lambda loaded: self.keywords.ops(loaded, item_id, patch)
            )
            return await self._keyword(scope, changed[0])
        question = await self._update_question(scope, item_id, patch)
        await self._rebuild(scope)
        return question

    async def delete(self, scope: TenantScope, kind: TopicRefKind, item_id: str) -> None:
        # a deleted item is kept as rejected so the next crawl cannot propose it again
        await self.update(scope, kind, item_id, TopicRefPatch(status="rejected"))

    async def bulk_status(
        self, scope: TenantScope, kind: TopicRefKind, item_ids: list[str], status: str
    ) -> int:
        patch = TopicRefPatch(status=status)  # type: ignore[arg-type]
        if kind == "keyword":

            def ops(loaded: LoadedProfile) -> list[EditOp]:
                return [
                    op
                    for item_id in dict.fromkeys(item_ids)
                    for op in self.keywords.ops(loaded, item_id, patch)
                ]

            return len(await self._edit_keywords(scope, ops))
        updated = 0
        async with self.session_factory() as session, session.begin():
            for item_id in dict.fromkeys(item_ids):
                row = await self._question_row(session, scope, item_id)
                row.status = status
                updated += 1
        await self._rebuild(scope)
        return updated

    async def _edit_keywords(self, scope: TenantScope, build) -> list[str]:
        async with self.session_factory() as session, session.begin():
            loaded = await self.edits.load(session, scope)
            ops: list[EditOp] = build(loaded)
            await KeywordBook.clear_tombstones(session, scope, ops)
            patch = {KEYWORDS: {self._op_key(op): self._op_patch(op) for op in ops}}
            profile = await self.edits.commit(session, scope, loaded, ops, {}, patch, self.clock.now())
            await KeywordBook.mirror(session, scope, loaded.effective, profile, ops)
        await self.edits.rebuild_topics(scope)
        return [self._op_key(op) for op in ops]

    @staticmethod
    def _op_key(op: EditOp) -> str:
        return str(op.item["id"]) if op.op == "add" and op.item else str(op.item_id)

    @staticmethod
    def _op_patch(op: EditOp) -> dict[str, Any] | None:
        if op.op == "reject":
            return None
        if op.op == "add":
            return {key: value for key, value in (op.item or {}).items() if key != "id"}
        if op.op == "edit":
            return dict(op.fields or {})
        return {"status": "confirmed"}

    async def _keyword(self, scope: TenantScope, keyword_id: str) -> KeywordItem:
        async with self.session_factory() as session:
            loaded = await self.edits.load(session, scope)
        return next(item for item in self.keywords.items(loaded) if item.id == keyword_id)

    async def _create_question(self, scope: TenantScope, body: BuyerQuestionCreate) -> BuyerQuestionItem:
        now = self.clock.now()
        async with self.session_factory() as session, session.begin():
            profile = await self._optional_profile(session, scope)
            normalized, geo = normalise_question(body.text, profile.locations.cities if profile else [])
            intent, stage = classify_intent(body.text)
            values = {
                "workspace_id": scope.workspace_id,
                "client_id": scope.client_id,
                "text": body.text.strip(),
                "normalized_text": normalized,
                "language": body.language,
                "funnel_stage": (body.funnel_stage or stage).value,
                "intent": intent.value,
                "geo_modifier": geo,
                "source": "user",
                "status": "confirmed",
                "priority": body.priority,
                "meta": self._user_meta(profile, now),
            }
            statement = insert(QuestionRow).values(**values)
            # a rejected question the user adds again comes back confirmed; a live one is a conflict
            statement = statement.on_conflict_do_update(
                index_elements=["client_id", "normalized_text", "language"],
                set_={"status": "confirmed", "priority": body.priority, "updated_at": now},
                where=QuestionRow.status.in_(("rejected", "retired")),
            ).returning(QuestionRow)
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                raise TopicRefExists("this question is already in the list")
            return question_item(row)

    async def _update_question(
        self, scope: TenantScope, item_id: str, patch: TopicRefPatch
    ) -> BuyerQuestionItem:
        if patch.weight is not None:
            raise invalid("buyer questions carry a priority, not a weight", "weight")
        try:
            async with self.session_factory() as session, session.begin():
                row = await self._question_row(session, scope, item_id)
                if patch.text is not None:
                    if len(patch.text.strip()) < 5:
                        raise invalid("a question needs at least 5 characters", "text")
                    profile = await self._optional_profile(session, scope)
                    normalized, geo = normalise_question(
                        patch.text, profile.locations.cities if profile else []
                    )
                    row.text, row.normalized_text, row.geo_modifier = patch.text.strip(), normalized, geo
                if patch.status is not None:
                    row.status = patch.status
                if patch.priority is not None:
                    row.priority = patch.priority
                await session.flush()
                return question_item(row)
        except IntegrityError:
            raise TopicRefExists("another question already has this wording") from None

    @staticmethod
    async def _question_row(session: AsyncSession, scope: TenantScope, item_id: str) -> QuestionRow:
        row = await BuyerQuestionRepository(session, scope).get(ids.decode(ids.BUYER_QUESTION, item_id))
        if row is None:
            raise NotFound("buyer question not found")
        return row

    async def _optional_profile(self, session: AsyncSession, scope: TenantScope) -> BusinessProfile | None:
        try:
            return (await self.edits.load(session, scope)).effective
        except ProfileMissing:
            return None

    @staticmethod
    def _user_meta(profile: BusinessProfile | None, now: datetime) -> dict[str, Any]:
        meta = ClaimMeta(
            origin=ClaimOrigin.user,
            status=ClaimStatus.confirmed,
            confidence=1.0,
            locked=True,
            first_seen_version=profile.version if profile else 1,
            updated_at=now,
        )
        return meta.model_dump(mode="json")

    async def _rebuild(self, scope: TenantScope) -> None:
        async with self.session_factory() as session:
            has_profile = await self._optional_profile(session, scope) is not None
        if has_profile:
            await self.edits.rebuild_topics(scope)


@api_service_registry.register(TOPIC_REFS)
def build_topic_ref_service(container: Container) -> TopicRefReadService:
    return TopicRefReadService(container)
