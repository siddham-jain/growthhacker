import hashlib
from functools import lru_cache

from app.core.config import TopicEngineSettings
from app.ports.llm import TaskId
from app.services.topics.lexicon import load_lexicons
from app.services.topics.prompt_specs import SPECS

PREPARER_RELEASE = "p1"
ENGINE_RELEASE = "e1"
DIRECTNESS_RELEASE = "d1"
ALIAS_PROMPT_VERSION = "topic_alias_proposal.v1"
TIEBREAK_PROMPT_VERSION = "directness_tiebreak.v1"


@lru_cache(maxsize=1)
def preparer_version() -> str:
    return f"{PREPARER_RELEASE}+{load_lexicons().content_hash}"


def engine_version(settings: TopicEngineSettings) -> str:
    # thresholds, calibration and the adjudication prompt are part of the version so a change triggers a rematch
    material = settings.model_dump_json() + SPECS[TaskId.topic_adjudication].prompt_version
    tuning = hashlib.sha256(material.encode()).hexdigest()[:8]
    return f"{ENGINE_RELEASE}+{load_lexicons().content_hash}+{tuning}"


@lru_cache(maxsize=1)
def directness_version() -> str:
    return f"{DIRECTNESS_RELEASE}+{load_lexicons().content_hash}"
