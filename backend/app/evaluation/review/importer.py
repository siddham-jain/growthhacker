import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from app.evaluation.packs.exports import PackCsvError, read_pack_csv
from app.evaluation.packs.participants import participant_id
from app.evaluation.review.models import Rating
from app.evaluation.review.pack import REVIEW_SCHEMA
from app.models.evaluation import REVIEW_CRITERIA

BOOLS = {"true": True, "false": False, "yes": True, "no": False}


@dataclass(frozen=True)
class RatingImport:
    ratings: list[Rating]
    skipped: int


def import_ratings(
    text: str, *, rater: str, known_item_ids: Collection[uuid.UUID], rated_at: datetime
) -> RatingImport:
    """rows with no answer are skipped; a row with some criteria but not all is rejected (edge case #38)."""
    rater_id = participant_id(rater)
    ratings, skipped = [], 0
    for line, row in enumerate(read_pack_csv(text, REVIEW_SCHEMA), 2):
        try:
            item_id = uuid.UUID(row["item_id"])
        except ValueError:
            raise PackCsvError(f"line {line}: item_id is not a uuid") from None
        if item_id not in known_item_ids:
            raise PackCsvError(f"line {line}: item {item_id} is not in this batch")
        answered = [c for c in REVIEW_CRITERIA if row[c]]
        if not answered and not row["would_ship"]:
            skipped += 1
            continue
        if len(answered) != len(REVIEW_CRITERIA) or row["would_ship"].lower() not in BOOLS:
            missing = [c for c in (*REVIEW_CRITERIA, "would_ship") if not row[c]]
            raise PackCsvError(f"line {line}: incomplete rating, missing {missing}")
        scores = {}
        for criterion in REVIEW_CRITERIA:
            value = row[criterion]
            if value not in {"1", "2", "3", "4", "5"}:
                raise PackCsvError(f"line {line}: {criterion} must be 1 to 5, got '{value}'")
            scores[criterion] = int(value)
        ratings.append(
            Rating(
                item_id=item_id,
                rater_id=rater_id,
                would_ship=BOOLS[row["would_ship"].lower()],
                comment=row["comment"] or None,
                rated_at=rated_at,
                **scores,
            )
        )
    return RatingImport(ratings=ratings, skipped=skipped)
