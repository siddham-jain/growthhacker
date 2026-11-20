from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.labelling.models import (
    GoldLabel,
    LabelRecord,
    OutlierEvalResult,
    Population,
    SampleDesign,
    TaskRecord,
)
from app.models.evaluation import Label, LabelGold, LabelTask
from app.models.evaluation import OutlierEvalResult as OutlierEvalRow


class LabelStore:
    """label_task, label, label_gold and outlier_eval_result; the caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_design(self, design: SampleDesign, population: Population, retention_days: int) -> int:
        for task in design.tasks:
            video = task.video
            self.session.add(
                LabelTask(
                    task_id=task.task_id,
                    batch_id=task.batch_id,
                    video_id=video.video_id,
                    channel_id=video.channel_id,
                    stratum=task.stratum,
                    inclusion_prob=task.inclusion_prob,
                    detector_ratio=video.ratio,
                    detector_label=video.is_outlier,
                    detector_version=population.detector_version,
                    observed_at=population.observed_at,
                    expires_at=video.oldest_api_at + timedelta(days=retention_days),
                )
            )
        await self.session.flush()
        return len(design.tasks)

    async def latest_batch_id(self) -> UUID | None:
        statement = select(LabelTask.batch_id).order_by(LabelTask.observed_at.desc()).limit(1)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def tasks(self, batch_id: UUID) -> list[TaskRecord]:
        rows = (
            (await self.session.execute(select(LabelTask).where(LabelTask.batch_id == batch_id)))
            .scalars()
            .all()
        )
        return [
            TaskRecord(
                task_id=r.task_id,
                batch_id=r.batch_id,
                video_id=r.video_id,
                channel_id=r.channel_id,
                stratum=r.stratum,
                inclusion_prob=r.inclusion_prob,
                detector_ratio=r.detector_ratio,
                detector_label=r.detector_label,
                detector_version=r.detector_version,
                observed_at=r.observed_at,
            )
            for r in sorted(rows, key=lambda r: str(r.task_id))
        ]

    async def task_for_video(self, video_id: str, channel_ids: Sequence[str]) -> TaskRecord | None:
        statement = (
            select(LabelTask)
            .where(LabelTask.video_id == video_id, LabelTask.channel_id.in_(list(channel_ids)))
            .order_by(LabelTask.observed_at.desc())
            .limit(1)
        )
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        return next(t for t in await self.tasks(row.batch_id) if t.task_id == row.task_id)

    async def save_labels(self, labels: Sequence[LabelRecord]) -> int:
        for label in labels:
            statement = insert(Label).values(
                task_id=label.task_id,
                labeller_id=label.labeller_id,
                round=label.round,
                label=label.label,
                reason_codes=list(label.reason_codes),
                note=label.note,
                seconds_spent=label.seconds_spent,
                labelled_at=label.labelled_at,
            )
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[Label.task_id, Label.labeller_id, Label.round],
                    set_={
                        name: statement.excluded[name]
                        for name in ("label", "reason_codes", "note", "seconds_spent", "labelled_at")
                    },
                )
            )
        return len(labels)

    async def labels(self, task_ids: Sequence[UUID]) -> list[LabelRecord]:
        if not task_ids:
            return []
        rows = (
            (await self.session.execute(select(Label).where(Label.task_id.in_(list(task_ids)))))
            .scalars()
            .all()
        )
        return [
            LabelRecord(
                task_id=r.task_id,
                labeller_id=r.labeller_id,
                round=r.round,
                label=r.label,
                reason_codes=tuple(r.reason_codes),
                note=r.note,
                seconds_spent=r.seconds_spent,
                labelled_at=r.labelled_at,
            )
            for r in sorted(rows, key=lambda r: (str(r.task_id), str(r.labeller_id), r.round))
        ]

    async def save_gold(self, gold: Sequence[GoldLabel]) -> int:
        # pending items have no gold yet; they are re-resolved on the next score
        written = 0
        for item in gold:
            if item.method == "pending":
                continue
            statement = insert(LabelGold).values(
                task_id=item.task_id, gold=item.gold or "excluded", method=item.method
            )
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[LabelGold.task_id],
                    set_={"gold": statement.excluded.gold, "method": statement.excluded.method},
                )
            )
            written += 1
        return written

    async def save_result(self, result: OutlierEvalResult) -> None:
        payload = result.model_dump(mode="json")
        curve = payload.pop("curve")
        self.session.add(
            OutlierEvalRow(
                batch_id=result.batch_id,
                detector_version=result.detector_version,
                metrics=payload,
                curve=curve,
            )
        )
        await self.session.flush()

    async def latest_result(self) -> OutlierEvalResult | None:
        statement = (
            select(OutlierEvalRow).order_by(OutlierEvalRow.created_at.desc(), OutlierEvalRow.id).limit(1)
        )
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        return OutlierEvalResult.model_validate({**row.metrics, "curve": row.curve})
