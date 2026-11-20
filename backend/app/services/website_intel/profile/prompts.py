from pathlib import Path

from pydantic import BaseModel

from app.ports.llm import PayloadClass, PromptRef, TaskId
from app.services.llm.prompts import PromptSpec, load_prompt_text, prompt_registry
from app.services.website_intel.profile.schemas import (
    ClaimsOutput,
    CoreProfileOutput,
    EnrichmentOutput,
    FreshnessOutput,
    PageFactsOutput,
    PageSelectionOutput,
)

PROMPT_DIR = Path(__file__).parent / "prompts"
WEB = PayloadClass.public_web
YOUTUBE = PayloadClass.public_youtube
PROFILE = PayloadClass.client_profile
PROFILE_CLAIMS_PROMPT = "website_profile_claims"
# prompt id -> (task, output schema, payload classes); r1 is two partitioned calls under one task
PROMPTS: dict[str, tuple[TaskId, type[BaseModel], frozenset[PayloadClass]]] = {
    TaskId.website_page_selection.value: (
        TaskId.website_page_selection,
        PageSelectionOutput,
        frozenset({WEB}),
    ),
    TaskId.website_page_map.value: (TaskId.website_page_map, PageFactsOutput, frozenset({WEB, YOUTUBE})),
    TaskId.website_profile_reduce.value: (
        TaskId.website_profile_reduce,
        CoreProfileOutput,
        frozenset({WEB, YOUTUBE}),
    ),
    PROFILE_CLAIMS_PROMPT: (TaskId.website_profile_reduce, ClaimsOutput, frozenset({WEB, YOUTUBE})),
    TaskId.website_keyword_enrichment.value: (
        TaskId.website_keyword_enrichment,
        EnrichmentOutput,
        frozenset({WEB, YOUTUBE, PROFILE}),
    ),
    TaskId.website_freshness_profile.value: (
        TaskId.website_freshness_profile,
        FreshnessOutput,
        frozenset({WEB, PROFILE}),
    ),
}
WEBSITE_PROMPTS: tuple[str, ...] = tuple(PROMPTS)

SPECS: dict[str, PromptSpec] = {
    prompt_id: prompt_registry.register(
        PromptSpec(
            prompt_id=prompt_id,
            task=task,
            version=1,
            system_text=load_prompt_text(f"{prompt_id}_v1.md", PROMPT_DIR),
            payload_classes=payload,
            output_schema=schema,
            changelog="initial website intelligence prompt",
        )
    )
    for prompt_id, (task, schema, payload) in PROMPTS.items()
}


def prompt_version(prompt_id: str) -> str:
    # any edit to the text or schema changes the version, which invalidates cached map results
    return SPECS[prompt_id].prompt_version


def prompt_ref(prompt_id: str) -> PromptRef:
    return PromptRef(prompt_id=prompt_id)
