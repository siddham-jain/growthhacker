import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert

import app.evaluation.api  # noqa: F401  (registers 12's evaluation service)
from app.models.evaluation import Label, LabelTask
from tests.api.conftest import Harness
from tests.api_services.conftest import tenant

pytestmark = pytest.mark.db
CHANNEL = "UC" + "a" * 22
LABELS = "/api/v1/evaluation/outlier-labels"


async def test_outlier_labels_are_private_to_their_workspace(harness: Harness) -> None:
    victim = await tenant(harness, "victimA")
    attacker = await tenant(harness, "attackB")
    for owner in (victim, attacker):
        attached = await harness.client.post(
            f"{owner.path}/channel/attach", json={"channel_id": CHANNEL}, headers=owner.headers
        )
        assert attached.status_code == 200, attached.text
    video_id = f"vid{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with harness.container.session_factory() as session, session.begin():
        task_id = (
            await session.execute(
                insert(LabelTask)
                .values(
                    batch_id=uuid.uuid4(),
                    video_id=video_id,
                    channel_id=CHANNEL,
                    stratum="x",
                    inclusion_prob=1.0,
                    detector_label=True,
                    detector_version="v",
                    observed_at=now,
                    expires_at=now + timedelta(days=5),
                )
                .returning(LabelTask.task_id)
            )
        ).scalar_one()
    try:
        private = {"is_outlier": True, "labeller": "alice", "note": "A-PRIVATE-NOTE"}
        assert (
            await harness.client.put(f"{LABELS}/{video_id}", json=private, headers=victim.headers)
        ).status_code == 200

        seen_by_attacker = await harness.client.get(LABELS, headers=attacker.headers)
        assert "A-PRIVATE-NOTE" not in seen_by_attacker.text
        assert all(item["note"] is None for item in seen_by_attacker.json()["items"])

        overwrite = {"is_outlier": False, "labeller": "alice", "note": "overwritten by B"}
        await harness.client.put(f"{LABELS}/{video_id}", json=overwrite, headers=attacker.headers)
        items = (await harness.client.get(LABELS, headers=victim.headers)).json()["items"]
        mine = next(item for item in items if item["video_id"] == video_id)
        assert mine["note"] == "A-PRIVATE-NOTE" and mine["is_outlier"] is True
    finally:
        async with harness.container.session_factory() as session, session.begin():
            await session.execute(delete(Label).where(Label.task_id == task_id))
            await session.execute(delete(LabelTask).where(LabelTask.task_id == task_id))
