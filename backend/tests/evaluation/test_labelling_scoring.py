import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import LabellingSettings
from app.evaluation.labelling.importer import import_labels
from app.evaluation.labelling.models import LabelRecord, TaskRecord, ThresholdPoint
from app.evaluation.labelling.pack import LABEL_SCHEMA
from app.evaluation.labelling.render import render_outlier_eval
from app.evaluation.labelling.scorer import choose_threshold, evaluate_labels, resolve_gold, threshold_grid
from app.evaluation.packs.exports import PackCsvError, write_pack_csv
from app.evaluation.packs.participants import participant_id

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
BATCH = uuid.UUID("01900000-0000-7000-8000-000000000001")
A, B, C = participant_id("owner"), participant_id("mira"), participant_id("adjudicator")
SETTINGS = LabellingSettings(bootstrap_resamples=200)


def task(
    n: int, ratio: float, stratum: str = "S1|R2", prob: float = 0.5, channel: int | None = None
) -> TaskRecord:
    return TaskRecord(
        task_id=uuid.UUID(int=n + 1),
        batch_id=BATCH,
        video_id=f"v{n:010d}",
        channel_id=f"UC{(channel if channel is not None else n):022d}",
        stratum=stratum,
        inclusion_prob=prob,
        detector_ratio=ratio,
        detector_label=ratio >= 3.0,
        detector_version="det1",
        observed_at=NOW,
    )


def label(t: TaskRecord, who: uuid.UUID, value: str, round_no: int = 1, reasons: tuple = ()) -> LabelRecord:
    return LabelRecord(
        task_id=t.task_id, labeller_id=who, round=round_no, label=value, reason_codes=reasons, labelled_at=NOW
    )


def test_gold_resolution_rules() -> None:
    tasks = [task(i, 3.0) for i in range(5)]
    labels = [
        label(tasks[0], A, "outlier"),
        label(tasks[0], B, "outlier"),
        label(tasks[1], A, "unsure"),
        label(tasks[1], B, "unsure"),
        label(tasks[2], A, "outlier"),
        label(tasks[2], B, "not_outlier"),
        label(tasks[2], C, "not_outlier", round_no=2),
        label(tasks[3], A, "unsure"),
        label(tasks[3], B, "outlier"),
        label(tasks[4], A, "outlier"),
    ]
    gold = resolve_gold(tasks, labels)
    assert [gold[t.task_id].method for t in tasks] == [
        "agree",
        "excluded",
        "adjudicated",
        "pending",
        "pending",
    ]
    assert gold[tasks[2].task_id].gold == "not_outlier"


def test_single_labeller_gives_single_gold() -> None:
    t = task(0, 4.0)
    gold = resolve_gold([t], [label(t, A, "outlier")])
    assert gold[t.task_id].method == "single"


def test_threshold_rule_prefers_higher_theta_on_ties_and_enforces_precision() -> None:
    def point(theta: float, precision: float, f05: float) -> ThresholdPoint:
        return ThresholdPoint(
            threshold=theta,
            precision_w=precision,
            recall_w=0.5,
            f05_w=f05,
            f1_w=0.5,
            precision_ci=None,
            recall_ci=None,
        )

    points = [point(2.0, 0.70, 0.9), point(3.0, 0.85, 0.8), point(3.5, 0.9, 0.8), point(4.0, 0.95, 0.6)]
    assert choose_threshold(points, 0.80) == 3.5
    assert choose_threshold([point(2.0, 0.5, 0.9)], 0.80) is None
    assert threshold_grid(SETTINGS)[0] == 1.5 and threshold_grid(SETTINGS)[-1] == 6.0
    assert len(threshold_grid(SETTINGS)) == 19


def test_weighted_precision_recall_and_report() -> None:
    # detector flags ratios >= 3; gold says outliers are ratios >= 4
    tasks = [
        task(0, 5.0, "S1|R1", 0.5),
        task(1, 4.5, "S1|R1", 0.5),
        task(2, 3.2, "S2|R2", 0.25),
        task(3, 2.5, "S2|R3", 0.25),
        task(4, 4.2, "S2|R3", 0.25),
        task(5, 1.0, "S3|R4", 0.1),
        task(6, 0.8, "S3|R4", 0.1),
    ]
    truth = {
        0: "outlier",
        1: "outlier",
        2: "not_outlier",
        3: "not_outlier",
        4: "outlier",
        5: "not_outlier",
        6: "not_outlier",
    }
    labels = [
        label(tasks[i], who, value, reasons=("format_mismatch",) if i == 2 else ())
        for i, value in truth.items()
        for who in (A, B)
    ]
    result, gold = evaluate_labels(
        BATCH,
        tasks,
        labels,
        SETTINGS,
        threshold=3.0,
        seed=1,
        clients_by_channel={tasks[0].channel_id: ["c1"]},
    )
    # flagged: 0,1,2,4 (weights 2,2,4,4); true positives 0,1,4 -> precision (2+2+4)/(2+2+4+4)
    assert result.precision_w == pytest.approx(8 / 12)
    assert result.recall_w == pytest.approx(1.0)
    assert result.confusion_raw == {"tp": 3, "fp": 1, "fn": 0, "tn": 3}
    assert result.agreement.cohen_kappa == pytest.approx(1.0)
    assert result.recommended_threshold == 4.0
    assert result.reason_codes["false_positive"] == {"format_mismatch": 2}
    assert result.by_client["c1"]["n"] == 1
    assert result.precision_ci is not None
    assert len(result.curve) == 19
    assert set(gold) == {t.task_id for t in tasks}
    markdown = render_outlier_eval(result)
    assert "Recommended θ: **4.00**" in markdown
    assert "4 ←" in markdown


def test_low_kappa_and_unsure_warnings() -> None:
    tasks = [task(i, 3.5 if i % 2 else 1.0) for i in range(10)]
    labels = []
    for i, t in enumerate(tasks):
        labels.append(label(t, A, "outlier" if i < 5 else "not_outlier"))
        labels.append(label(t, B, "unsure" if i < 3 else ("not_outlier" if i < 7 else "outlier")))
        if i < 3:
            labels[-2] = label(t, A, "unsure")
    result, _ = evaluate_labels(BATCH, tasks, labels, SETTINGS, threshold=3.0, seed=1)
    joined = " ".join(result.warnings)
    assert "relabel a fresh 30" in joined
    assert "excluded as unsure" in joined


def test_importer_round_trip_and_validation() -> None:
    tasks = [task(0, 3.0), task(1, 1.0)]
    known = {t.task_id for t in tasks}
    rows = [
        {
            "task_id": str(tasks[0].task_id),
            "label": "outlier",
            "reason_codes": "collab;too_young",
            "note": "",
            "seconds_spent": "31",
        },
        {"task_id": str(tasks[1].task_id), "label": "", "reason_codes": "", "note": "", "seconds_spent": ""},
    ]
    result = import_labels(
        write_pack_csv(rows, LABEL_SCHEMA), labeller="Mira", round_no=1, known_task_ids=known, labelled_at=NOW
    )
    assert result.skipped == 1
    [only] = result.labels
    assert only.labeller_id == participant_id("mira")
    assert only.reason_codes == ("collab", "too_young")
    assert only.seconds_spent == 31
    bad_label = [{**rows[0], "label": "maybe"}]
    with pytest.raises(PackCsvError, match="label must be"):
        import_labels(
            write_pack_csv(bad_label, LABEL_SCHEMA),
            labeller="m",
            round_no=1,
            known_task_ids=known,
            labelled_at=NOW,
        )
    bad_reason = [{**rows[0], "reason_codes": "vibes"}]
    with pytest.raises(PackCsvError, match="unknown reason"):
        import_labels(
            write_pack_csv(bad_reason, LABEL_SCHEMA),
            labeller="m",
            round_no=1,
            known_task_ids=known,
            labelled_at=NOW,
        )
    with pytest.raises(PackCsvError, match="not in this batch"):
        import_labels(
            write_pack_csv(rows[:1], LABEL_SCHEMA),
            labeller="m",
            round_no=1,
            known_task_ids=set(),
            labelled_at=NOW,
        )
