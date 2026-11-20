import hashlib
from functools import lru_cache

from app.core.config import TopicEngineSettings
from app.services.topics.lexicon import load_lexicons

PREPARER_RELEASE = "p1"
ENGINE_RELEASE = "e1"
DIRECTNESS_RELEASE = "d1"
ADJUDICATION_PROMPT_VERSION = "topic_adjudication.v1"
ALIAS_PROMPT_VERSION = "topic_alias_proposal.v1"
TIEBREAK_PROMPT_VERSION = "directness_tiebreak.v1"


@lru_cache(maxsize=1)
def preparer_version() -> str:
    return f"{PREPARER_RELEASE}+{load_lexicons().content_hash}"


def engine_version(settings: TopicEngineSettings) -> str:
    # thresholds and calibration are part of the version so a recalibration triggers a rematch
    tuning = hashlib.sha256(settings.model_dump_json().encode()).hexdigest()[:8]
    return f"{ENGINE_RELEASE}+{load_lexicons().content_hash}+{tuning}"


@lru_cache(maxsize=1)
def directness_version() -> str:
    return f"{DIRECTNESS_RELEASE}+{load_lexicons().content_hash}"
