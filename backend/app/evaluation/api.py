from collections import Counter
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.api.errors import NotFound
from app.api.ids import RUN, encode
from app.api.pagination import Page, PageRequest
from app.api.schemas.admin import LabelIn, LabelOut, PrecisionOut, ReviewIn
from app.api.services import EVALUATION, api_service_registry
from app.core.tenancy import TenantScope
from app.evaluation.backtest.models import BacktestConfig
from app.evaluation.backtest.runner import eval_id_for
from app.evaluation.labelling.api import LabellingApi
from app.evaluation.review.api import ReviewApi
from app.models.evaluation import EvalConfig, EvalPatternSpec, EvalResult


class BacktestApi:
    """what one backtest run registered and, once scored, its pooled results."""

    def __init__(self, container: Any) -> None:
        self.session_factory = container.session_factory

    async def backtest_report(self, scope: TenantScope, run_id: UUID) -> dict[str, Any]:
        eval_id = eval_id_for(run_id)
        async with self.session_factory() as session:
            row = await session.get(EvalConfig, eval_id)
            if row is None or row.config.get("workspace_id") != str(scope.workspace_id):
                raise NotFound("this backtest has not recorded its arms yet")
            arms = Counter(
                (
                    await session.execute(
                        select(EvalPatternSpec.arm).where(EvalPatternSpec.eval_id == eval_id)
                    )
                ).scalars()
            )
            results = (
                (await session.execute(select(EvalResult).where(EvalResult.eval_id == eval_id)))
                .scalars()
                .all()
            )
        config = BacktestConfig.model_validate(row.config)
        return {
            "run_id": encode(RUN, run_id),
            "eval_id": str(eval_id),
            "status": row.status,
            "registered_at": row.registered_at.isoformat(),
            "config_hash": row.config_hash,
            "as_of": config.as_of.isoformat(),
            "outcomes_mature_at": config.outcomes_mature_at().isoformat(),
            "specs_by_arm": dict(sorted(arms.items())),
            "results": [
                {
                    "scope": r.scope,
                    "arm": r.arm,
                    "metric": r.metric,
                    "estimate": r.estimate,
                    "ci": None if r.ci_low is None else [r.ci_low, r.ci_high],
                    "n_videos": r.n_videos,
                    "n_channels": r.n_channels,
                    "p_value": r.p_value,
                    "p_adjusted": r.p_adj,
                    "notes": r.notes,
                }
                for r in results
            ],
        }


class EvaluationApiService:
    """11's EvaluationService: backtests, outlier labels and human review, each owned by its own module."""

    def __init__(self, container: Any) -> None:
        self.backtests = BacktestApi(container)
        self.labelling = LabellingApi(container)
        self.review = ReviewApi(container)

    async def backtest_report(self, scope: TenantScope, run_id: UUID) -> dict[str, Any]:
        return await self.backtests.backtest_report(scope, run_id)

    async def labels(self, scope: TenantScope, client_id: UUID | None, page: PageRequest) -> Page[LabelOut]:
        return await self.labelling.labels(scope, client_id, page)

    async def put_label(self, scope: TenantScope, video_id: str, body: LabelIn) -> LabelOut:
        return await self.labelling.put_label(scope, video_id, body)

    async def precision(self, scope: TenantScope, client_id: UUID | None) -> PrecisionOut:
        return await self.labelling.precision(scope, client_id)

    async def review_queue(self, scope: TenantScope) -> dict[str, Any]:
        return await self.review.review_queue(scope)

    async def submit_review(self, scope: TenantScope, body: ReviewIn) -> dict[str, Any]:
        return await self.review.submit_review(scope, body)

    async def review_summary(self, scope: TenantScope) -> dict[str, Any]:
        return await self.review.review_summary(scope)


@api_service_registry.register(EVALUATION)
def build_evaluation_service(container: Any) -> EvaluationApiService:
    return EvaluationApiService(container)
