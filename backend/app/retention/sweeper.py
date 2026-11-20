from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import PolicySettings, RetentionSettings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.retention.partitions import drop_partitions_before, ensure_partitions
from app.retention.rules import RetentionAction, RetentionRule, retention_registry

logger = get_logger(__name__)

REFRESHABLE_KEYS = {"yt_channels": "channel_id", "yt_videos": "video_id"}
REFERENCED_CHANNELS_SQL = (
    "SELECT channel_id FROM v_tracked_channels "
    "UNION SELECT unnest(pinned_channel_ids) FROM pipeline_runs "
    "WHERE status IN ('queued', 'running', 'cancelling')"
)
REFERENCED_VIDEOS_SQL = (
    f"SELECT video_id FROM yt_videos WHERE channel_id IN ({REFERENCED_CHANNELS_SQL}) "
    "UNION SELECT unnest(referenced_video_ids) FROM recommendations WHERE redacted_at IS NULL"
)
REFERENCED_SQL = {"yt_channels": REFERENCED_CHANNELS_SQL, "yt_videos": REFERENCED_VIDEOS_SQL}


class CacheRefresher(Protocol):
    async def schedule(self, table: str, ids: Sequence[str]) -> None: ...


@dataclass
class TableOutcome:
    deleted: int = 0
    redacted: int = 0
    refresh_scheduled: int = 0
    due: int = 0
    partitions_dropped: list[str] = field(default_factory=list)


@dataclass
class RetentionReport:
    started_at: datetime
    finished_at: datetime | None = None
    per_rule: dict[str, TableOutcome] = field(default_factory=dict)
    oldest_age: dict[str, timedelta] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)


def _where(rule: RetentionRule, extra: str | None = None) -> str:
    clauses = [f"({rule.clock}) < :cutoff"] if rule.clock else []
    clauses += [f"({condition})" for condition in (rule.condition, extra) if condition]
    return " AND ".join(clauses) or "TRUE"


class RetentionSweeper:
    def __init__(
        self,
        session_factory: SessionFactory,
        clock: Clock,
        policy: PolicySettings,
        settings: RetentionSettings,
        refresher: CacheRefresher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._policy = policy
        self._settings = settings
        self._refresher = refresher

    async def sweep(self, rules: Sequence[RetentionRule] | None = None) -> RetentionReport:
        rules = list(retention_registry.all().values()) if rules is None else list(rules)
        report = RetentionReport(started_at=self._clock.now())
        for rule in rules:
            report.per_rule[rule.name] = await self._apply(rule)
        report.oldest_age, report.violations = await self.audit(rules)
        report.finished_at = self._clock.now()
        for violation in report.violations:
            logger.error("retention.violation", detail=violation)
        return report

    async def _apply(self, rule: RetentionRule) -> TableOutcome:
        match rule.action:
            case RetentionAction.drop_partition:
                return await self._partitions(rule)
            case RetentionAction.delete:
                return TableOutcome(
                    deleted=await self._delete_batches(rule, rule.cutoff(self._now, self._policy))
                )
            case RetentionAction.redact:
                return TableOutcome(redacted=await self._redact_batches(rule))
            case RetentionAction.refresh_or_delete:
                return await self._refresh_or_delete(rule)
            case RetentionAction.verify_authorization:
                return TableOutcome(redacted=await self._invalidate_unverified_tokens(rule))
            case RetentionAction.reverify_ids:
                return await self._reverify_ids(rule)
            case _:
                return TableOutcome()

    @property
    def _now(self) -> datetime:
        return self._clock.now()

    async def _partitions(self, rule: RetentionRule) -> TableOutcome:
        async with self._session_factory() as session, session.begin():
            await ensure_partitions(
                session,
                rule.table,
                today=self._now.date(),
                days_ahead=self._settings.snapshot_partition_days_ahead,
            )
            # plain detach keeps this inside one short transaction; concurrently needs autocommit
            dropped = await drop_partitions_before(
                session, rule.table, cutoff=rule.cutoff(self._now, self._policy)
            )
        return TableOutcome(partitions_dropped=dropped)

    async def _delete_batches(self, rule: RetentionRule, cutoff: datetime, extra: str | None = None) -> int:
        statement = text(
            f"DELETE FROM {rule.table} WHERE ctid IN "
            f"(SELECT ctid FROM {rule.table} WHERE {_where(rule, extra)} LIMIT :batch)"
        )
        return await self._run_batches(statement, cutoff)

    async def _redact_batches(self, rule: RetentionRule) -> int:
        assignments = ", ".join(f"{column} = {value}" for column, value in rule.redact_set.items())
        statement = text(
            f"UPDATE {rule.table} SET {assignments} WHERE ctid IN "
            f"(SELECT ctid FROM {rule.table} WHERE {_where(rule)} LIMIT :batch)"
        )
        return await self._run_batches(statement, rule.cutoff(self._now, self._policy))

    async def _run_batches(self, statement, cutoff: datetime) -> int:
        total = 0
        batch = self._settings.delete_batch_size
        while True:
            async with self._session_factory() as session, session.begin():
                result = await session.execute(statement, {"cutoff": cutoff, "batch": batch})
            total += result.rowcount
            if result.rowcount < batch:
                return total

    async def _refresh_or_delete(self, rule: RetentionRule) -> TableOutcome:
        outcome = TableOutcome()
        key = REFRESHABLE_KEYS[rule.table]
        referenced = REFERENCED_SQL[rule.table]
        hard_limit = rule.hard_limit(self._policy)
        if hard_limit is not None:
            outcome.deleted += await self._delete_batches(rule, self._now - hard_limit)
        due_cutoff = rule.cutoff(self._now, self._policy) + timedelta(
            hours=self._settings.refresh_lookahead_hours
        )
        unreferenced = (
            f"NOT EXISTS (SELECT 1 FROM ({referenced}) AS referenced(id) WHERE referenced.id = {key})"
        )
        outcome.deleted += await self._delete_batches(rule, due_cutoff, unreferenced)
        async with self._session_factory() as session:
            rows = await session.execute(
                text(f"SELECT {key} FROM {rule.table} WHERE {_where(rule)} ORDER BY {rule.clock}"),
                {"cutoff": due_cutoff},
            )
            due_ids = [row[0] for row in rows]
        outcome.due = len(due_ids)
        if self._refresher is not None and due_ids:
            size = self._settings.refresh_batch_size
            for start in range(0, len(due_ids), size):
                await self._refresher.schedule(rule.table, due_ids[start : start + size])
            outcome.refresh_scheduled = len(due_ids)
        return outcome

    async def _invalidate_unverified_tokens(self, rule: RetentionRule) -> int:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                text(
                    f"UPDATE {rule.table} SET status = 'invalid', revocation_source = 'invalid_grant', "
                    "purge_due_at = now(), updated_at = now() "
                    f"WHERE status = 'active' AND ({rule.clock}) < :cutoff"
                ),
                {"cutoff": rule.cutoff(self._now, self._policy)},
            )
        return result.rowcount

    async def _reverify_ids(self, rule: RetentionRule) -> TableOutcome:
        """re-verified ids keep their row; unverifiable ones are refreshed, and redacted at the hard limit."""
        cutoff = rule.cutoff(self._now, self._policy)
        hard_limit = rule.hard_limit(self._policy)
        hard_cutoff = self._now - hard_limit if hard_limit is not None else cutoff
        key = REFRESHABLE_KEYS[rule.id_source]
        verified = (
            f"SELECT {key} FROM {rule.id_source} WHERE availability = 'available' "
            f"AND LEAST(metadata_fetched_at, stats_fetched_at) >= :cutoff"
        )
        if rule.id_arrays:
            refs = " || ".join(rule.id_columns)
            unverified = f"SELECT ref.id FROM unnest({refs}) AS ref(id) WHERE ref.id NOT IN ({verified})"
            all_verified = f"NOT EXISTS ({unverified})"
            unverified_ids = f"SELECT DISTINCT ref.id FROM {rule.table}, unnest({refs}) AS ref(id)"
            unverified_ids += f" WHERE ({rule.clock}) < :cutoff AND ref.id NOT IN ({verified})"
            kept = ", ".join(
                f"{column} = ARRAY(SELECT ref FROM unnest({column}) AS ref WHERE ref IN ({verified}))"
                for column in rule.id_columns
            )
            redact = f"UPDATE {rule.table} SET {kept}, {rule.clock} = :now WHERE ({rule.clock}) < :hard"
        else:
            (column,) = rule.id_columns
            all_verified = f"{column} IN ({verified})"
            unverified_ids = (
                f"SELECT DISTINCT {column} FROM {rule.table} "
                f"WHERE ({rule.clock}) < :cutoff AND {column} NOT IN ({verified})"
            )
            gone = f"({rule.clock}) < :hard AND {column} NOT IN ({verified})"
            if rule.redact_set:
                assignments = ", ".join(f"{name} = {value}" for name, value in rule.redact_set.items())
                redact = f"UPDATE {rule.table} SET {assignments} WHERE {gone}"
            else:
                redact = f"DELETE FROM {rule.table} WHERE {gone}"
        params = {"cutoff": cutoff, "hard": hard_cutoff, "now": self._now}
        outcome = TableOutcome(due=await self._count(rule, cutoff))
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    f"UPDATE {rule.table} SET {rule.clock} = :now "
                    f"WHERE ({rule.clock}) < :cutoff AND {all_verified}"
                ),
                params,
            )
            outcome.redacted = (await session.execute(text(redact), params)).rowcount
            pending = [row[0] for row in await session.execute(text(unverified_ids), params)]
        if self._refresher is not None and pending:
            size = self._settings.refresh_batch_size
            for start in range(0, len(pending), size):
                await self._refresher.schedule(rule.id_source, pending[start : start + size])
            outcome.refresh_scheduled = len(pending)
        return outcome

    async def _count(self, rule: RetentionRule, cutoff: datetime) -> int:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    text(f"SELECT count(*) FROM {rule.table} WHERE {_where(rule)}"), {"cutoff": cutoff}
                )
            ).scalar_one()

    async def audit(self, rules: Sequence[RetentionRule]) -> tuple[dict[str, timedelta], list[str]]:
        oldest: dict[str, timedelta] = {}
        violations: list[str] = []
        async with self._session_factory() as session:
            for rule in rules:
                limit = rule.hard_limit(self._policy)
                if limit is None:
                    continue
                age = await self._oldest_age(session, rule)
                if age is None:
                    continue
                oldest[rule.name] = age
                if age > limit:
                    violations.append(f"{rule.name}: oldest row is {age} old (limit {limit})")
        return oldest, violations

    async def _oldest_age(self, session: AsyncSession, rule: RetentionRule) -> timedelta | None:
        filters = f"WHERE {rule.condition}" if rule.condition else ""
        oldest_clock = (
            await session.execute(text(f"SELECT min({rule.clock}) FROM {rule.table} {filters}"))
        ).scalar_one()
        return self._now - oldest_clock if isinstance(oldest_clock, datetime) else None
