import gzip
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.core.config import Settings
from app.pipeline.contracts import RunConfigSnapshot
from app.services.analytics.inputs import AnalyticsInputs

PitLevel = Literal["strict", "approximate"]
MANIFEST_FILE = "manifest.json"
INPUTS_FILE = "inputs.json.gz"


class LeakageError(RuntimeError):
    """a frozen bundle holds a row observed after its point in time."""


class FreezeManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    freeze_id: UUID
    workspace_id: UUID
    client_id: UUID
    as_of: AwareDatetime
    created_at: AwareDatetime
    channel_ids: list[str]
    own_channel_id: str | None
    competitor_set_version: int
    topic_ref_version: int
    business_profile_version: int
    gate_config_version: str
    analytics_config_hash: str
    record_counts: dict[str, int]
    oldest_api_fetched_at: AwareDatetime
    expires_at: AwareDatetime
    pit_level: PitLevel
    late_tables: list[str] = []


class FreezeBundle(BaseModel):
    """everything the analysis reads at T: the run config as it stood and the inputs visible at T."""

    model_config = ConfigDict(frozen=True)

    manifest: FreezeManifest
    snapshot: RunConfigSnapshot
    inputs: AnalyticsInputs


def late_rows(inputs: AnalyticsInputs, as_of: datetime) -> dict[str, int]:
    """rows observed after as_of, per table; a strict freeze has none."""
    counts = {
        "videos_published": sum(v.published_at > as_of for v in inputs.videos),
        "videos_fetched": sum(v.metadata_fetched_at > as_of for v in inputs.videos),
        "channels_fetched": sum(
            max(c.channel.metadata_fetched_at, c.channel.stats_fetched_at) > as_of for c in inputs.channels
        ),
        "snapshots": sum(s.captured_at > as_of for snaps in inputs.snapshots.values() for s in snaps),
        "comments": sum(c.fetched_at > as_of for items in inputs.comments.values() for c in items),
        "thumbnails": sum(t.computed_at > as_of for t in inputs.thumbnails.values()),
        "topic_matches": sum(m.matched_at > as_of for m in inputs.topic_matches),
    }
    return {table: n for table, n in counts.items() if n}


def oldest_api_datum(inputs: AnalyticsInputs, fallback: datetime) -> datetime:
    stamps = [v.metadata_fetched_at for v in inputs.videos]
    stamps += [c.channel.stats_fetched_at for c in inputs.channels]
    stamps += [s.captured_at for snaps in inputs.snapshots.values() for s in snaps]
    stamps += [c.fetched_at for items in inputs.comments.values() for c in items]
    return min(stamps, default=fallback)


def prune_expired(inputs: AnalyticsInputs, cutoff: datetime) -> tuple[AnalyticsInputs, int]:
    """drops api rows fetched before cutoff: a bundle never serves a row past its own 28 days."""
    videos = [v for v in inputs.videos if v.metadata_fetched_at >= cutoff]
    ids = {v.video_id for v in videos}
    snapshots = {
        vid: [s for s in items if s.captured_at >= cutoff]
        for vid, items in inputs.snapshots.items()
        if vid in ids
    }
    comments = {
        vid: [c for c in items if c.fetched_at >= cutoff]
        for vid, items in inputs.comments.items()
        if vid in ids
    }
    kept = len(videos) + sum(map(len, snapshots.values())) + sum(map(len, comments.values()))
    before = (
        len(inputs.videos)
        + sum(map(len, inputs.snapshots.values()))
        + sum(map(len, inputs.comments.values()))
    )
    pruned = inputs.model_copy(
        update={
            "videos": videos,
            "snapshots": snapshots,
            "comments": comments,
            "thumbnails": {vid: t for vid, t in inputs.thumbnails.items() if vid in ids},
            "topic_matches": [m for m in inputs.topic_matches if m.video_id in ids],
            "directness": [d for d in inputs.directness if d.video_id in ids],
        }
    )
    return pruned, before - kept


class BundleStore:
    """one directory per freeze, named by freeze id, so expiry can delete it even after the row was redacted."""

    def __init__(self, root: Path, max_row_age: timedelta) -> None:
        self.root = root
        self.max_row_age = max_row_age

    @classmethod
    def for_settings(cls, settings: Settings) -> "BundleStore":
        return cls(settings.evaluation.bundle_dir, timedelta(days=settings.policy.retention_days))

    def path_for(self, freeze_id: UUID) -> Path:
        return self.root / str(freeze_id)

    def write(self, bundle: FreezeBundle) -> Path:
        directory = self.path_for(bundle.manifest.freeze_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "snapshot": bundle.snapshot.model_dump(mode="json"),
            "inputs": bundle.inputs.model_dump(mode="json"),
        }
        with gzip.open(directory / INPUTS_FILE, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        (directory / MANIFEST_FILE).write_text(bundle.manifest.model_dump_json(indent=2), encoding="utf-8")
        return directory

    def read(self, freeze_id: UUID, now: datetime) -> FreezeBundle:
        directory = self.path_for(freeze_id)
        if not (directory / MANIFEST_FILE).exists():
            raise FileNotFoundError(f"freeze {freeze_id} has no bundle (expired, purged or never written)")
        manifest = FreezeManifest.model_validate_json((directory / MANIFEST_FILE).read_text("utf-8"))
        if now >= manifest.expires_at:
            raise FileNotFoundError(f"freeze {freeze_id} expired at {manifest.expires_at.isoformat()}")
        with gzip.open(directory / INPUTS_FILE, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        inputs, _ = prune_expired(AnalyticsInputs.model_validate(payload["inputs"]), now - self.max_row_age)
        bundle = FreezeBundle(
            manifest=manifest, snapshot=RunConfigSnapshot.model_validate(payload["snapshot"]), inputs=inputs
        )
        assert_point_in_time(bundle)
        return bundle

    def prune(self, freeze_id: UUID, now: datetime) -> int:
        """rewrites the bundle without rows past their 28 days (nightly, so nothing lingers at rest)."""
        bundle = self.read(freeze_id, now)
        directory = self.path_for(freeze_id)
        with gzip.open(directory / INPUTS_FILE, "rt", encoding="utf-8") as handle:
            stored = AnalyticsInputs.model_validate(json.load(handle)["inputs"])
        _, dropped = prune_expired(stored, now - self.max_row_age)
        if dropped:
            self.write(bundle)
        return dropped

    def delete(self, freeze_id: UUID) -> bool:
        directory = self.path_for(freeze_id)
        if not directory.exists():
            return False
        shutil.rmtree(directory)
        return True

    def stored_ids(self) -> list[UUID]:
        if not self.root.exists():
            return []
        return sorted(UUID(p.name) for p in self.root.iterdir() if p.is_dir() and _is_uuid(p.name))


def assert_point_in_time(bundle: FreezeBundle) -> None:
    # the reader is the leakage guard: nothing published, captured or computed after T may be replayed
    late = late_rows(bundle.inputs, bundle.manifest.as_of)
    allowed = set(bundle.manifest.late_tables)
    leaking = {table: n for table, n in late.items() if table not in allowed}
    if leaking:
        raise LeakageError(f"freeze {bundle.manifest.freeze_id} holds post-T rows: {leaking}")


def _is_uuid(name: str) -> bool:
    try:
        UUID(name)
    except ValueError:
        return False
    return True
