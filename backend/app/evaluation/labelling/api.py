from typing import Any
from uuid import UUID

from app.api.errors import BadRequest, NotFound
from app.api.pagination import Page, PageRequest
from app.api.schemas.admin import LabelIn, LabelOut, PrecisionOut
from app.core.tenancy import TenantScope
from app.evaluation.labelling.models import LabelRecord
from app.evaluation.labelling.store import LabelStore
from app.evaluation.labelling.universe import clients_by_channel
from app.evaluation.packs.participants import participant_id
from app.services.analytics.settings import AnalyticsEngineSettings

OUTLIER_FLAGS = {"outlier": True, "not_outlier": False, "unsure": None}
CURSOR_PREFIX = "o:"


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.startswith(CURSOR_PREFIX) or not cursor[len(CURSOR_PREFIX) :].isdigit():
        raise BadRequest("the cursor is invalid")
    return int(cursor[len(CURSOR_PREFIX) :])


class LabellingApi:
    """outlier labels for 11's /evaluation routes; tasks are scoped through the workspace's channels, labels
    through their workspace_id."""

    def __init__(self, container: Any) -> None:
        self.session_factory = container.session_factory
        self.settings = container.settings
        self.clock = container.clock

    async def labels(self, scope: TenantScope, client_id: UUID | None, page: PageRequest) -> Page[LabelOut]:
        async with self.session_factory() as session:
            store = LabelStore(session)
            channels = await clients_by_channel(session, scope.workspace_id, client_id)
            batch_id = await store.latest_batch_id()
            tasks = [] if batch_id is None else await store.tasks(batch_id)
            tasks = [t for t in tasks if t.video_id and t.channel_id in channels]
            labels = await store.labels([t.task_id for t in tasks], workspace_id=scope.workspace_id)
        latest: dict[UUID, LabelRecord] = {}
        for label in sorted(labels, key=lambda lb: (lb.round, lb.labelled_at)):
            latest[label.task_id] = label
        start = _offset(page.cursor)
        window = tasks[start : start + page.limit]
        items = [
            self._out(task.video_id or "", channels.get(task.channel_id or "", []), latest.get(task.task_id))
            for task in window
        ]
        more = start + page.limit < len(tasks)
        return Page(
            items=items,
            next_cursor=f"{CURSOR_PREFIX}{start + page.limit}" if more else None,
            limit=page.limit,
            total=len(tasks),
        )

    async def put_label(self, scope: TenantScope, video_id: str, body: LabelIn) -> LabelOut:
        now = self.clock.now()
        async with self.session_factory() as session, session.begin():
            channels = await clients_by_channel(session, scope.workspace_id)
            store = LabelStore(session)
            task = await store.task_for_video(video_id, list(channels))
            if task is None:
                raise NotFound("this video is not in a labelling batch for this workspace")
            label = LabelRecord(
                task_id=task.task_id,
                # labeller names are per workspace, so two workspaces' "alice" never share a label row
                labeller_id=participant_id(f"{scope.workspace_id}:{body.labeller}"),
                round=1,
                label="outlier" if body.is_outlier else "not_outlier",
                note=body.note,
                labelled_at=now,
                workspace_id=scope.workspace_id,
            )
            await store.save_labels([label])
        return self._out(video_id, channels.get(task.channel_id or "", []), label)

    async def precision(self, scope: TenantScope, client_id: UUID | None) -> PrecisionOut:
        async with self.session_factory() as session:
            result = await LabelStore(session).latest_result()
        if result is None:
            threshold = AnalyticsEngineSettings.from_settings(self.settings).outliers.outlier_ratio
            return PrecisionOut(precision=None, recall=None, n=0, threshold=threshold)
        if client_id is not None:
            group = result.by_client.get(str(client_id))
            if group is None:
                return PrecisionOut(precision=None, recall=None, n=0, threshold=result.threshold)
            return PrecisionOut(
                precision=group["precision_w"],
                recall=group["recall_w"],
                n=int(group["n"] or 0),
                threshold=result.threshold,
            )
        return PrecisionOut(
            precision=result.precision_w, recall=result.recall_w, n=result.n_gold, threshold=result.threshold
        )

    @staticmethod
    def _out(video_id: str, client_ids: list[str], label: LabelRecord | None) -> LabelOut:
        return LabelOut(
            video_id=video_id,
            client_id=client_ids[0] if len(client_ids) == 1 else None,
            is_outlier=OUTLIER_FLAGS[label.label] if label else None,
            labeller=str(label.labeller_id) if label else None,
            note=label.note if label else None,
            labelled_at=label.labelled_at if label else None,
        )
