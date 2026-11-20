import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from app.evaluation.labelling.guidelines import LABELS, REASON_CODES
from app.evaluation.labelling.models import LabelRecord
from app.evaluation.labelling.pack import LABEL_SCHEMA
from app.evaluation.packs.exports import PackCsvError, read_pack_csv
from app.evaluation.packs.participants import participant_id


@dataclass(frozen=True)
class LabelImport:
    labels: list[LabelRecord]
    skipped: int


def parse_seconds(value: str) -> int | None:
    if not value:
        return None
    try:
        seconds = int(float(value))
    except ValueError:
        raise PackCsvError(f"seconds_spent '{value}' is not a number") from None
    return max(seconds, 0)


def import_labels(
    text: str,
    *,
    labeller: str,
    round_no: int,
    known_task_ids: Collection[uuid.UUID],
    labelled_at: datetime,
) -> LabelImport:
    if round_no < 1:
        raise ValueError("round starts at 1")
    labeller_id = participant_id(labeller)
    labels, skipped = [], 0
    for line, row in enumerate(read_pack_csv(text, LABEL_SCHEMA), 2):
        try:
            task_id = uuid.UUID(row["task_id"])
        except ValueError:
            raise PackCsvError(f"line {line}: task_id is not a uuid") from None
        if task_id not in known_task_ids:
            raise PackCsvError(f"line {line}: task {task_id} is not in this batch")
        if not row["label"]:
            skipped += 1
            continue
        if row["label"] not in LABELS:
            raise PackCsvError(f"line {line}: label must be one of {', '.join(LABELS)}")
        reasons = tuple(sorted({code for code in row["reason_codes"].split(";") if code}))
        unknown = [code for code in reasons if code not in REASON_CODES]
        if unknown:
            raise PackCsvError(f"line {line}: unknown reason codes {unknown}")
        labels.append(
            LabelRecord(
                task_id=task_id,
                labeller_id=labeller_id,
                round=round_no,
                label=row["label"],
                reason_codes=reasons,
                note=row["note"] or None,
                seconds_spent=parse_seconds(row["seconds_spent"]),
                labelled_at=labelled_at,
            )
        )
    return LabelImport(labels=labels, skipped=skipped)
