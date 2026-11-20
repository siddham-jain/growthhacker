from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.recommendations import MatchSpec
from app.evaluation.backtest.models import ArmSpec, BacktestConfig, BacktestReport, OutcomeVideo
from app.models.evaluation import EvalConfig, EvalOutcomeVideo, EvalPatternSpec, EvalResult

INSERT_CHUNK = 2000


class ConfigChanged(RuntimeError):
    """the registered config no longer hashes the same; a new evaluation id is required (edge case 15)."""


class BacktestStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(self, eval_id: UUID, config: BacktestConfig, now: datetime) -> EvalConfig:
        digest = config.canonical_hash()
        row = await self.session.get(EvalConfig, eval_id)
        if row is not None:
            if row.config_hash != digest:
                raise ConfigChanged(f"evaluation {eval_id} is registered with another config")
            return row
        row = EvalConfig(
            eval_id=eval_id,
            mode=config.mode,
            config=config.model_dump(mode="json"),
            config_hash=digest,
            code_version=config.code_version,
            seed=config.seed,
            registered_at=now,
            status="registered",
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def config(self, eval_id: UUID) -> BacktestConfig:
        row = await self.session.get(EvalConfig, eval_id)
        if row is None:
            raise LookupError(f"evaluation {eval_id} is not registered")
        config = BacktestConfig.model_validate(row.config)
        if config.canonical_hash() != row.config_hash:
            raise ConfigChanged(f"evaluation {eval_id} config changed after registration; register a new one")
        return config

    async def listed(self) -> list[EvalConfig]:
        return list(
            (
                await self.session.execute(select(EvalConfig).order_by(EvalConfig.registered_at.desc()))
            ).scalars()
        )

    async def replace_specs(
        self, eval_id: UUID, config: BacktestConfig, specs: list[ArmSpec], expires_at: datetime
    ) -> int:
        await self.session.execute(delete(EvalPatternSpec).where(EvalPatternSpec.eval_id == eval_id))
        rows = [
            {
                "eval_id": eval_id,
                "spec_id": item.spec.spec_id,
                "workspace_id": config.workspace_id,
                "client_id": config.client_id,
                "arm": item.arm,
                "replicate": item.replicate,
                "spec": {
                    "match_spec": item.spec.model_dump(mode="json"),
                    "confidence": item.confidence,
                    "expected_low": item.expected_low,
                    "expected_high": item.expected_high,
                },
                "specificity": item.spec.specificity,
                "source_ref": item.spec.source_ref,
                "channel_id": item.spec.channel_id,
                "expires_at": expires_at,
            }
            for item in specs
        ]
        for start in range(0, len(rows), INSERT_CHUNK):
            await self.session.execute(insert(EvalPatternSpec), rows[start : start + INSERT_CHUNK])
        return len(rows)

    async def specs(self, eval_id: UUID) -> list[ArmSpec]:
        rows = (
            await self.session.execute(
                select(EvalPatternSpec)
                .where(EvalPatternSpec.eval_id == eval_id)
                .order_by(EvalPatternSpec.arm, EvalPatternSpec.replicate, EvalPatternSpec.spec_id)
            )
        ).scalars()
        specs = []
        for row in rows:
            spec = MatchSpec.model_validate(row.spec["match_spec"])
            if row.channel_id is None and spec.channel_id is not None:
                # creator channel ids are nulled at expiry; such a spec can no longer be matched
                continue
            specs.append(
                ArmSpec(
                    arm=row.arm,
                    spec=spec,
                    replicate=row.replicate,
                    confidence=row.spec.get("confidence"),
                    expected_low=row.spec.get("expected_low"),
                    expected_high=row.spec.get("expected_high"),
                )
            )
        return specs

    async def save_outcomes(
        self, eval_id: UUID, outcomes: list[OutcomeVideo], matched: dict[str, list[str]], expires_at: datetime
    ) -> None:
        await self.session.execute(delete(EvalOutcomeVideo).where(EvalOutcomeVideo.eval_id == eval_id))
        rows = [
            {
                "eval_id": eval_id,
                "client_id": o.client_id,
                "video_id": o.video_id,
                "channel_id": o.channel_id,
                "published_at": o.published_at,
                "views_at_age": o.views_at_age,
                "method": o.views_at_age_method,
                "baseline": o.baseline_at_t,
                "ratio": o.ratio,
                "is_outlier": o.is_outlier,
                "matched": {"arms": matched.get(o.video_id, [])},
                "status": o.status,
                "expires_at": expires_at,
            }
            for o in outcomes
        ]
        for start in range(0, len(rows), INSERT_CHUNK):
            await self.session.execute(insert(EvalOutcomeVideo), rows[start : start + INSERT_CHUNK])

    async def save_results(self, eval_id: UUID, report: BacktestReport) -> None:
        await self.session.execute(delete(EvalResult).where(EvalResult.eval_id == eval_id))
        rows = []
        for arm in report.arms:
            for metric, value, ci in (
                ("outlier_rate", arm.outlier_rate, arm.outlier_rate_ci),
                ("geo_mean_ratio", arm.geo_mean_ratio, arm.geo_mean_ratio_ci),
                ("lift_vs_base", arm.lift_vs_base, arm.lift_vs_base_ci),
                ("coverage", arm.coverage, None),
            ):
                rows.append(
                    _result(
                        eval_id,
                        arm.scope,
                        arm.arm,
                        metric,
                        value,
                        ci,
                        arm.n_videos_matched,
                        arm.n_channels_matched,
                    )
                )
        for comparison in report.comparisons:
            row = _result(
                eval_id,
                comparison.scope,
                f"{comparison.a}-{comparison.b}",
                comparison.metric,
                comparison.estimate,
                comparison.ci,
                0,
                0,
            )
            rows.append(
                row
                | {"p_value": comparison.p_value, "p_adj": comparison.p_adjusted, "notes": comparison.method}
            )
        rows.append(
            _result(eval_id, "pooled", "all", "verdict", None, None, 0, 0) | {"notes": report.verdict}
        )
        await self.session.execute(insert(EvalResult), rows)

    async def mark(self, eval_id: UUID, status: str, **stamps: datetime) -> None:
        await self.session.execute(
            update(EvalConfig).where(EvalConfig.eval_id == eval_id).values(status=status, **stamps)
        )


def _result(
    eval_id: UUID,
    scope: str,
    arm: str,
    metric: str,
    value: float | None,
    ci: tuple[float, float] | None,
    n_videos: int,
    n_channels: int,
) -> dict:
    return {
        "eval_id": eval_id,
        "scope": scope,
        "arm": arm,
        "metric": metric,
        "estimate": value,
        "ci_low": ci[0] if ci else None,
        "ci_high": ci[1] if ci else None,
        "n_videos": n_videos,
        "n_channels": n_channels,
        "p_value": None,
        "p_adj": None,
        "notes": None,
    }
