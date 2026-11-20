import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

import numpy as np

from app.core.config import LabellingSettings
from app.evaluation.labelling.models import Population, PopulationVideo, SampleDesign, SampledTask
from app.evaluation.stats.sampling import inclusion_probability

# subscriber order, so neighbours are adjacent; hidden counts (s0) sit next to the smallest band
SIZE_BANDS = ("S0", "S1", "S2", "S3", "S4")
RATIO_BANDS = ("R1", "R2", "R3", "R4")
SHORTS = "SH"
SHORT_SEGMENT = "short_form"


def size_band(video: PopulationVideo) -> str:
    count = video.subscriber_count
    if video.hidden_subscribers or count is None:
        return "S0"
    if count < 10_000:
        return "S1"
    if count < 100_000:
        return "S2"
    if count < 1_000_000:
        return "S3"
    return "S4"


def ratio_band(ratio: float, threshold: float) -> str:
    if ratio >= 2 * threshold:
        return "R1"
    if ratio >= threshold:
        return "R2"
    if ratio >= 0.6 * threshold:
        return "R3"
    return "R4"


def largest_remainder(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    weight_sum = sum(weights.values())
    if total <= 0 or weight_sum <= 0:
        return dict.fromkeys(weights, 0)
    exact = {key: total * w / weight_sum for key, w in weights.items()}
    counts = {key: int(value) for key, value in exact.items()}
    rest = total - sum(counts.values())
    for key in sorted(exact, key=lambda k: (-(exact[k] - counts[k]), k))[:rest]:
        counts[key] += 1
    return counts


def allocate_band(total: int, capacity: Mapping[str, int], min_per_cell: int) -> dict[str, int]:
    """split one ratio band over size bands: a floor per non-empty cell, then even shares, with any
    share a small cell cannot hold moved to its nearest neighbour with room."""
    cells = [band for band in SIZE_BANDS if capacity.get(band, 0) > 0]
    allocation = dict.fromkeys(SIZE_BANDS, 0)
    if not cells or total <= 0:
        return allocation
    remaining = total
    for band in cells:
        take = min(min_per_cell, capacity[band], remaining)
        allocation[band] += take
        remaining -= take
    for band, extra in largest_remainder(remaining, dict.fromkeys(cells, 1.0)).items():
        allocation[band] += extra
    for band in cells:
        overflow = allocation[band] - capacity[band]
        if overflow <= 0:
            continue
        allocation[band] = capacity[band]
        origin = SIZE_BANDS.index(band)
        for neighbour in sorted(cells, key=lambda b: (abs(SIZE_BANDS.index(b) - origin), b)):
            room = capacity[neighbour] - allocation[neighbour]
            if room > 0 and overflow > 0:
                moved = min(room, overflow)
                allocation[neighbour] += moved
                overflow -= moved
    return allocation


class OutlierSampleDesigner:
    """12 §5.2: ratio band x size band strata that oversample positives and near misses, plus a separate
    shorts stratum (99 c13); inclusion probabilities give horvitz-thompson weights."""

    def __init__(self, settings: LabellingSettings) -> None:
        self.settings = settings

    def design(self, population: Population, seed: int, batch_id: uuid.UUID | None = None) -> SampleDesign:
        batch = batch_id or uuid.uuid4()
        rng = np.random.default_rng(seed)
        threshold = population.threshold
        cells: dict[str, list[PopulationVideo]] = defaultdict(list)
        for video in population.videos:
            band = ratio_band(video.ratio, threshold)
            key = f"{SHORTS}|{band}" if video.segment == SHORT_SEGMENT else f"{size_band(video)}|{band}"
            cells[key].append(video)
        targets: dict[str, int] = {}
        for band in RATIO_BANDS:
            capacity = {size: len(cells.get(f"{size}|{band}", [])) for size in SIZE_BANDS}
            for size, count in allocate_band(
                self.settings.ratio_band_allocation.get(band, 0), capacity, self.settings.min_per_cell
            ).items():
                if count:
                    targets[f"{size}|{band}"] = count
        shorts_split = largest_remainder(
            self.settings.shorts_sample_size,
            {band: float(n) for band, n in self.settings.ratio_band_allocation.items()},
        )
        for band, count in shorts_split.items():
            available = len(cells.get(f"{SHORTS}|{band}", []))
            if min(count, available):
                targets[f"{SHORTS}|{band}"] = min(count, available)
        per_channel: Counter[str] = Counter()
        tasks: list[SampledTask] = []
        sampled: dict[str, int] = {}
        shortfall: dict[str, int] = {}
        chosen: dict[str, list[PopulationVideo]] = {}
        for key in sorted(targets):
            members = sorted(cells[key], key=lambda v: v.video_id)
            picks: list[PopulationVideo] = []
            for index in rng.permutation(len(members)):
                video = members[index]
                if len(picks) == targets[key]:
                    break
                if per_channel[video.channel_id] >= self.settings.max_per_channel:
                    continue
                per_channel[video.channel_id] += 1
                picks.append(video)
            chosen[key] = picks
            sampled[key] = len(picks)
            if len(picks) < targets[key]:
                shortfall[key] = targets[key] - len(picks)
        for key, picks in chosen.items():
            probability = inclusion_probability(len(picks), len(cells[key])) if picks else 0.0
            tasks.extend(
                SampledTask(
                    task_id=uuid.uuid4(),
                    batch_id=batch,
                    video=video,
                    stratum=key,
                    inclusion_prob=probability,
                )
                for video in picks
            )
        notes = self._notes(population, cells, shortfall)
        return SampleDesign(
            batch_id=batch,
            tasks=tuple(sorted(tasks, key=lambda t: (t.stratum, t.video.video_id))),
            cell_population={key: len(members) for key, members in sorted(cells.items())},
            cell_sampled=dict(sorted(sampled.items())),
            shortfall=dict(sorted(shortfall.items())),
            notes=notes,
        )

    def _notes(
        self,
        population: Population,
        cells: Mapping[str, Sequence[PopulationVideo]],
        shortfall: Mapping[str, int],
    ) -> tuple[str, ...]:
        notes = []
        present = {key.split("|")[0] for key, members in cells.items() if members}
        notes += [f"no {band} channels in the population" for band in SIZE_BANDS if band not in present]
        if population.excluded_no_ratio:
            notes.append(f"{population.excluded_no_ratio} videos had no detector ratio and were not sampled")
        if shortfall:
            notes.append(
                f"{sum(shortfall.values())} slots unfilled (small cells or the "
                f"{self.settings.max_per_channel}-per-channel cap)"
            )
        return tuple(notes)
