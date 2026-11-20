"""frozen bundles built from 06's apollo golden inputs, frozen twelve days before the golden as_of."""

import uuid
from datetime import timedelta

from app.evaluation.freeze.bundle import FreezeBundle, FreezeManifest, late_rows
from app.evaluation.freeze.job import record_counts
from app.pipeline.contracts import Market, RunConfigSnapshot
from app.services.analytics.inputs import AnalyticsInputs
from tests.analytics.factories import AS_OF, CLIENT_ID
from tests.analytics.test_golden import build_inputs

FREEZE_AT = AS_OF - timedelta(days=12)
WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


def snapshot(inputs: AnalyticsInputs, client_id: uuid.UUID = CLIENT_ID) -> RunConfigSnapshot:
    return RunConfigSnapshot(
        workspace_id=WORKSPACE_ID,
        client_id=client_id,
        config_version=1,
        config_hash="h",
        window_days=30,
        own_channel_id=inputs.own_channel_id,
        competitor_channel_ids=[
            c.channel.channel_id for c in inputs.channels if c.channel.channel_id != inputs.own_channel_id
        ],
        topic_refs=list(inputs.topic_refs),
        topic_set_version=1,
        market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
    )


def bundle(
    inputs: AnalyticsInputs | None = None,
    *,
    client_id: uuid.UUID = CLIENT_ID,
    workspace_id: uuid.UUID = WORKSPACE_ID,
    freeze_id: uuid.UUID | None = None,
) -> FreezeBundle:
    visible = (inputs or build_inputs()).visible_at(FREEZE_AT)
    # the golden data was fetched after FREEZE_AT, so this is an approximate freeze, as FreezeJob would mark it
    late = late_rows(visible, FREEZE_AT)
    manifest = FreezeManifest(
        freeze_id=freeze_id or uuid.uuid4(),
        workspace_id=workspace_id,
        client_id=client_id,
        as_of=FREEZE_AT,
        created_at=FREEZE_AT,
        channel_ids=sorted(c.channel.channel_id for c in visible.channels),
        own_channel_id=visible.own_channel_id,
        competitor_set_version=1,
        topic_ref_version=1,
        business_profile_version=1,
        gate_config_version="g",
        analytics_config_hash="a",
        record_counts=record_counts(visible),
        oldest_api_fetched_at=FREEZE_AT - timedelta(days=1),
        expires_at=FREEZE_AT + timedelta(days=27),
        pit_level="approximate" if late else "strict",
        late_tables=sorted(late),
    )
    return FreezeBundle(
        manifest=manifest,
        snapshot=snapshot(visible, client_id).model_copy(update={"workspace_id": workspace_id}),
        inputs=visible,
    )
