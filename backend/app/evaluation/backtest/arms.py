"""naive baselines next to the system arm (plan 12 §5.1.2 step 4); new baselines register, the runner is untouched."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.core.registry import Registry
from app.domain.common import FormatSegment
from app.domain.recommendations import MatchSpec
from app.evaluation.backtest.models import Arm, ArmSpec, CorpusVideo


@dataclass(frozen=True)
class ArmContext:
    system: Sequence[ArmSpec]
    window: Sequence[CorpusVideo]
    topic_ref_ids: Sequence[str]
    universe: Sequence[str]
    replicates: int
    seed: int


class ArmGenerator(Protocol):
    arm: Arm

    def generate(self, ctx: ArmContext) -> list[ArmSpec]: ...


arm_registry: Registry[type[ArmGenerator]] = Registry("backtest_arm_registry")


def specificity(
    topic_ids: Sequence[str],
    segment: FormatSegment | None,
    length_band: str | None,
    title: dict[str, bool | str],
    thumbnail: dict[str, bool | str],
    channel_id: str | None,
) -> int:
    return (
        len(topic_ids)
        + (segment is not None)
        + (length_band is not None)
        + len(title)
        + len(thumbnail)
        + (channel_id is not None)
    )


def build_spec(
    template: MatchSpec,
    spec_id: str,
    source: Arm,
    source_ref: str | None,
    *,
    topic_ids: Sequence[str],
    segment: FormatSegment | None,
    length_band: str | None,
    title: dict[str, bool | str],
    thumbnail: dict[str, bool | str],
    channel_id: str | None,
) -> MatchSpec:
    return MatchSpec(
        spec_id=spec_id,
        rec_type=template.rec_type,
        topic_ids=list(topic_ids),
        format=segment,
        length_band=length_band,
        title_traits=title,
        thumbnail_traits=thumbnail,
        channel_id=channel_id,
        lens=list(template.lens),
        specificity=specificity(topic_ids, segment, length_band, title, thumbnail, channel_id),
        source=source,
        source_ref=source_ref,
    )


def _by_type(specs: Sequence[ArmSpec]) -> dict[str, list[ArmSpec]]:
    grouped: dict[str, list[ArmSpec]] = defaultdict(list)
    for item in specs:
        grouped[item.spec.rec_type.value].append(item)
    return grouped


@arm_registry.register("copy_top")
class CopyTopArm:
    """'copy the most-viewed recent video': raw lifetime views at T, own uploads excluded, no format filter."""

    arm: Arm = "copy_top"

    def generate(self, ctx: ArmContext) -> list[ArmSpec]:
        ranked = sorted(
            (v for v in ctx.window if not v.is_own and v.views_at_t is not None),
            key=lambda v: (-(v.views_at_t or 0), v.published_at, v.video_id),
        )
        specs = []
        for rec_type, templates in sorted(_by_type(ctx.system).items()):
            for rank, (template, seed) in enumerate(zip(templates, ranked, strict=False)):
                specs.append(
                    ArmSpec(arm=self.arm, spec=self.fill(template.spec, seed, f"copy_top:{rec_type}:{rank}"))
                )
        return specs

    @staticmethod
    def fill(template: MatchSpec, seed: CorpusVideo, spec_id: str) -> MatchSpec:
        # the same template the system used for this type, filled from the seed video's own attributes
        best_topics = sorted(seed.topic_scores, key=lambda t: (-seed.topic_scores[t], t))
        title = {k: seed.traits[f"title.{k}"] for k in template.title_traits if f"title.{k}" in seed.traits}
        thumbnail = {
            k: seed.traits[f"thumbnail.{k}"]
            for k in template.thumbnail_traits
            if f"thumbnail.{k}" in seed.traits
        }
        return build_spec(
            template,
            spec_id,
            "copy_top",
            seed.video_id,
            topic_ids=sorted(best_topics[: len(template.topic_ids)]),
            segment=FormatSegment(seed.segment) if template.format is not None else None,
            length_band=seed.traits.get("length_band") if template.length_band is not None else None,
            title=title,
            thumbnail=thumbnail,
            channel_id=seed.channel_id if template.channel_id is not None else None,
        )


@dataclass(frozen=True)
class ObservedValues:
    """values seen among pre-T window videos, so every random constraint is feasible (edge case 21)."""

    segments: list[str]
    length_bands: list[str]
    traits: dict[str, list[str]]

    @classmethod
    def of(cls, window: Sequence[CorpusVideo]) -> "ObservedValues":
        traits: dict[str, set[str]] = defaultdict(set)
        for video in window:
            for key, value in video.traits.items():
                traits[key].add(value)
        return cls(
            segments=sorted({v.segment for v in window}),
            length_bands=sorted(traits.pop("length_band", set())),
            traits={key: sorted(values) for key, values in sorted(traits.items())},
        )


@arm_registry.register("random")
class RandomArm:
    """per system spec, R random specs of the same type and specificity; seeded per replicate."""

    arm: Arm = "random"

    def generate(self, ctx: ArmContext) -> list[ArmSpec]:
        observed = ObservedValues.of(ctx.window)
        streams = np.random.SeedSequence(ctx.seed).spawn(ctx.replicates)
        specs = []
        for replicate, stream in enumerate(streams):
            rng = np.random.default_rng(stream)
            for index, template in enumerate(ctx.system):
                spec = self.draw(template.spec, observed, ctx, rng, f"random:{replicate}:{index}")
                specs.append(ArmSpec(arm=self.arm, spec=spec, replicate=replicate))
        return specs

    @staticmethod
    def draw(
        template: MatchSpec, observed: ObservedValues, ctx: ArmContext, rng: np.random.Generator, spec_id: str
    ) -> MatchSpec:
        def pick(values: Sequence[str]) -> str | None:
            return str(values[int(rng.integers(0, len(values)))]) if values else None

        topics = list(ctx.topic_ref_ids)
        n_topics = min(len(template.topic_ids), len(topics))
        topic_ids = (
            sorted(str(t) for t in rng.choice(topics, size=n_topics, replace=False)) if n_topics else []
        )
        segment = pick(observed.segments) if template.format is not None else None
        length_band = pick(observed.length_bands) if template.length_band is not None else None
        title = {
            k: value
            for k in template.title_traits
            if (value := pick(observed.traits.get(f"title.{k}", []))) is not None
        }
        thumbnail = {
            k: value
            for k in template.thumbnail_traits
            if (value := pick(observed.traits.get(f"thumbnail.{k}", []))) is not None
        }
        channel_id = pick(sorted(ctx.universe)) if template.channel_id is not None else None
        return build_spec(
            template,
            spec_id,
            "random",
            spec_id.split(":")[1],
            topic_ids=topic_ids,
            segment=FormatSegment(segment) if segment else None,
            length_band=length_band,
            title=title,
            thumbnail=thumbnail,
            channel_id=channel_id,
        )


def generate_arms(ctx: ArmContext) -> list[ArmSpec]:
    specs = list(ctx.system)
    for generator in arm_registry.all().values():
        specs.extend(generator().generate(ctx))
    return specs
