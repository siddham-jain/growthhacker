"""video-by-arm membership over complete outcome videos, the shape every backtest metric is computed on."""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from app.evaluation.backtest.matcher import UnmatchableSpecType, matches
from app.evaluation.backtest.models import ArmSpec, OutcomeVideo


@dataclass
class ClientEvaluation:
    eval_id: str
    client_id: str
    specs: list[ArmSpec]
    outcomes: list[OutcomeVideo]
    replicates: int
    notes: list[str] = field(default_factory=list)
    invalid_reasons: list[str] = field(default_factory=list)


@dataclass
class SpecMatch:
    item: ArmSpec
    client_id: str
    rows: list[int]


@dataclass
class Frame:
    """rows are complete outcome videos of every client; masks say which arm's specs each row follows."""

    videos: list[OutcomeVideo]
    clients: np.ndarray
    channels: np.ndarray
    outlier: np.ndarray
    log_ratio: np.ndarray
    detector_outlier: np.ndarray
    system: np.ndarray
    copy_top: np.ndarray
    random: np.ndarray
    spec_matches: list[SpecMatch]
    unmatchable: list[str]

    @property
    def n(self) -> int:
        return len(self.videos)

    def mask(self, arm: str) -> np.ndarray:
        return {"system": self.system, "copy_top": self.copy_top, "base": np.ones(self.n, dtype=bool)}[arm]


def build_frame(evaluations: Sequence[ClientEvaluation]) -> Frame:
    complete = [(e, o) for e in evaluations for o in e.outcomes if o.status == "complete"]
    videos = [o for _, o in complete]
    replicates = max((e.replicates for e in evaluations), default=0)
    system = np.zeros(len(videos), dtype=bool)
    copy_top = np.zeros(len(videos), dtype=bool)
    random = np.zeros((replicates, len(videos)), dtype=bool)
    spec_matches: list[SpecMatch] = []
    unmatchable: list[str] = []
    for evaluation in evaluations:
        rows = [i for i, (e, _) in enumerate(complete) if e is evaluation]
        corpus = [(i, videos[i].as_corpus()) for i in rows]
        for item in evaluation.specs:
            try:
                hit = [i for i, video in corpus if matches(item.spec, video)]
            except UnmatchableSpecType as error:
                unmatchable.append(str(error))
                continue
            if item.arm == "random":
                random[item.replicate or 0, hit] = True
                continue
            (system if item.arm == "system" else copy_top)[hit] = True
            spec_matches.append(SpecMatch(item=item, client_id=evaluation.client_id, rows=hit))
    ratios = np.asarray([o.ratio for o in videos], dtype=float)
    return Frame(
        videos=videos,
        clients=np.asarray([str(o.client_id) for o in videos], dtype=object),
        channels=np.asarray([o.channel_id for o in videos], dtype=object),
        outlier=np.asarray([bool(o.is_outlier) for o in videos], dtype=bool),
        log_ratio=np.log(np.clip(ratios, 1e-9, None)) if videos else np.zeros(0),
        detector_outlier=np.asarray([bool(o.detector_is_outlier) for o in videos], dtype=bool),
        system=system,
        copy_top=copy_top,
        random=random,
        spec_matches=spec_matches,
        unmatchable=sorted(set(unmatchable)),
    )


def rate(values: np.ndarray, mask: np.ndarray, idx: np.ndarray) -> float | None:
    selected = values[idx][mask[idx]]
    return float(selected.mean()) if selected.size else None


def geo_mean(log_ratio: np.ndarray, mask: np.ndarray, idx: np.ndarray) -> float | None:
    selected = log_ratio[idx][mask[idx]]
    return float(np.exp(selected.mean())) if selected.size else None


def mean_log(log_ratio: np.ndarray, mask: np.ndarray, idx: np.ndarray) -> float | None:
    selected = log_ratio[idx][mask[idx]]
    return float(selected.mean()) if selected.size else None


def matched_arms(frame: Frame) -> dict[str, list[str]]:
    arms: dict[str, list[str]] = {}
    for row, video in enumerate(frame.videos):
        names = [name for name, mask in (("system", frame.system), ("copy_top", frame.copy_top)) if mask[row]]
        if frame.random.shape[0] and frame.random[:, row].any():
            names.append("random")
        arms[video.video_id] = names
    return arms
