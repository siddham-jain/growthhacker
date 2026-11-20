from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.api import ids
from app.api.errors import AppError
from app.api.schemas.common import Freshness, ResultEnvelope
from app.core.config import Settings
from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.domain.runs import RunNotice, RunNoticeCode
from app.models.runs import ActiveResultSet, PipelineRun


class ResultsNotReady(AppError):
    status = 404
    code = "results_not_ready"
    title = "No results have been published for this window yet"


class ResultsExpired(AppError):
    status = 410
    code = "data_stale"
    title = "These results aged out under the 30-day data rule"


@dataclass(frozen=True)
class ActiveResult:
    run: PipelineRun
    window_days: int
    published_at: datetime
    stale_reasons: list[str]
    previous_run_id: UUID | None

    @property
    def run_id(self) -> UUID:
        return self.run.id


async def active_result(
    session_factory: SessionFactory, scope: TenantScope, window_days: int
) -> ActiveResult:
    """readers resolve active_result_sets first, so they never observe a half-published run."""
    async with session_factory() as session:
        row = (
            await session.execute(
                select(ActiveResultSet, PipelineRun)
                .join(PipelineRun, PipelineRun.id == ActiveResultSet.run_id)
                .where(
                    ActiveResultSet.workspace_id == scope.workspace_id,
                    ActiveResultSet.client_id == scope.client_id,
                    ActiveResultSet.window_days == window_days,
                )
            )
        ).one_or_none()
    if row is None:
        raise ResultsNotReady(f"no {window_days}-day results have been published yet")
    result_set, run = row
    history = result_set.superseded_history or []
    previous = UUID(history[0]["run_id"]) if history and isinstance(history[0], dict) else None
    return ActiveResult(
        run=run,
        window_days=window_days,
        published_at=result_set.published_at,
        stale_reasons=list(result_set.stale_reasons or []),
        previous_run_id=previous,
    )


def envelope[T](data: T, active: ActiveResult, settings: Settings, now: datetime) -> ResultEnvelope[T]:
    as_of = active.run.as_of
    age = now - as_of
    if age > timedelta(days=settings.policy.retention_days):
        raise ResultsExpired("refresh the analysis to see current evidence")
    reasons = list(active.stale_reasons)
    if age > timedelta(hours=settings.pipeline.stale_after_h):
        reasons.append("refresh_overdue")
    notices = [RunNotice.model_validate(notice) for notice in active.run.notices or []]
    if "refresh_overdue" in reasons:
        notices.append(
            RunNotice(
                code=RunNoticeCode.data_stale,
                message=f"Last updated {age.days} days ago — we're retrying."
                if age.days
                else "These results are older than usual — we're retrying.",
                detail={"age_hours": round(age.total_seconds() / 3600, 1)},
            )
        )
    return ResultEnvelope[T](
        data=data,
        window_days=active.window_days,
        as_of=as_of,
        run_id=ids.encode(ids.RUN, active.run_id),
        generated_at=active.published_at,
        freshness=Freshness(
            data_through=as_of,
            age_hours=round(age.total_seconds() / 3600, 2),
            stale=bool(reasons),
            stale_reasons=reasons,
        ),
        notices=notices,
        prev_run_id=ids.encode_optional(ids.RUN, active.previous_run_id),
    )
