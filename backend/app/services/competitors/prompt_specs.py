from pathlib import Path

from app.ports.llm import PayloadClass, TaskId
from app.services.competitors.classify.relation_classifier import ClassificationBatch
from app.services.competitors.payloads import PAYLOADS_BY_TASK
from app.services.competitors.sources.extraction import ExtractedEntities
from app.services.competitors.sources.llm_source import LlmCandidateList
from app.services.competitors.summaries import SummaryBatch
from app.services.competitors.verify.adjudicator import Adjudication
from app.services.llm.prompts import PromptSpec, load_prompt_text, prompt_registry

PROMPT_DIR = Path(__file__).parent / "prompt_texts"
SCHEMAS = {
    TaskId.competitor_candidate_generation: LlmCandidateList,
    TaskId.competitor_entity_extraction: ExtractedEntities,
    TaskId.competitor_relation_classification: ClassificationBatch,
    TaskId.competitor_ownership_adjudication: Adjudication,
    TaskId.competitor_evidence_summary: SummaryBatch,
}

SPECS = {
    task: prompt_registry.register(
        PromptSpec(
            prompt_id=task.value,
            task=task,
            version=1,
            system_text=load_prompt_text(f"{task.value}_v1.md", PROMPT_DIR),
            payload_classes=frozenset(PAYLOADS_BY_TASK[task]) | {PayloadClass.public_web},
            output_schema=schema,
            changelog="initial competitor discovery prompt",
        )
    )
    for task, schema in SCHEMAS.items()
}
