import re

from app.domain.card_copy import Violation
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.validators.base import copy_fields, grounding_validators, masked_text

YOUTUBE_URL = re.compile(r"(youtube\.com/(watch|shorts/|embed/)|youtu\.be/)", re.I)
VIDEO_ID = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{11}(?![\w-])")
ALIAS = re.compile(r"\b[vfoqp]\d{1,2}\b")


def looks_like_video_id(token: str) -> bool:
    has_digit = any(char.isdigit() for char in token)
    has_upper = any(char.isupper() for char in token)
    has_lower = any(char.islower() for char in token)
    return has_digit and has_upper and has_lower


class VideoGrounding:
    """V2: reference videos are rendered by code, so copy never names ids, urls or pack aliases."""

    name = "videos"

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        violations = []
        for field in copy_fields(copy):
            text = masked_text(field.text, built)
            if YOUTUBE_URL.search(text):
                violations.append(
                    Violation(rule="video_id_in_text", field=field.name, detail="remove the video link")
                )
            for token in VIDEO_ID.findall(text):
                if looks_like_video_id(token):
                    violations.append(
                        Violation(
                            rule="video_id_in_text",
                            field=field.name,
                            token=token,
                            detail="remove the video id",
                        )
                    )
            for alias in ALIAS.findall(text):
                violations.append(
                    Violation(
                        rule="alias_in_text",
                        field=field.name,
                        token=alias,
                        detail=f"'{alias}' is an internal reference; cite it in fact_refs, not in the text",
                    )
                )
        return violations


grounding_validators.add("videos", lambda settings: VideoGrounding())
