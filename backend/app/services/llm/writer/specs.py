from pydantic import BaseModel

from app.ports.llm import PayloadClass, PromptInput, PromptRef, TaskId
from app.services.llm.prompts import PromptSpec, load_prompt_text, prompt_registry
from app.services.llm.writer.schemas import CardCopyLLM, CritiqueBatchLLM

CARD_WRITE_PROMPT = PromptRef(prompt_id="card_write")
CARD_CRITIQUE_PROMPT = PromptRef(prompt_id="card_critique")
CARD_INPUT_CLASSES = frozenset(
    {
        PayloadClass.public_youtube,
        PayloadClass.public_web,
        PayloadClass.news,
        PayloadClass.client_profile,
        PayloadClass.client_private_analytics,
    }
)


def card_count(prompt_input: PromptInput) -> int:
    return sum(1 for block in prompt_input.trusted if block.label.startswith("card_"))


def check_reviews(value: BaseModel, prompt_input: PromptInput) -> list[str]:
    assert isinstance(value, CritiqueBatchLLM)
    expected = {f"c{index}" for index in range(1, card_count(prompt_input) + 1)}
    seen = [review.card for review in value.reviews]
    problems = []
    if sorted(seen) != sorted(expected):
        problems.append(f"return exactly one review for each of: {', '.join(sorted(expected))}")
    return problems


CARD_WRITE_V1 = prompt_registry.register(
    PromptSpec(
        prompt_id="card_write",
        task=TaskId.card_write,
        version=1,
        system_text=load_prompt_text("card_write_v1.md"),
        examples=load_prompt_text("card_write_examples_v1.md"),
        payload_classes=CARD_INPUT_CLASSES,
        output_schema=CardCopyLLM,
        changelog="initial evidence-grounded writer",
    )
)
CARD_CRITIQUE_V1 = prompt_registry.register(
    PromptSpec(
        prompt_id="card_critique",
        task=TaskId.card_critique,
        version=1,
        system_text=load_prompt_text("card_critique_v1.md"),
        payload_classes=CARD_INPUT_CLASSES,
        output_schema=CritiqueBatchLLM,
        semantic_check=check_reviews,
        changelog="initial rubric critic",
    )
)
