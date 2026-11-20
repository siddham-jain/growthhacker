from pathlib import Path

from app.ports.llm import TaskId
from app.services.llm.prompts import PromptSpec, load_prompt_text, prompt_registry
from app.services.topics.llm import PAYLOAD

PROMPT_DIR = Path(__file__).parent / "prompts"
# output schemas are built per call from the client's own topic ids, so none is pinned here
TASKS = (TaskId.topic_adjudication, TaskId.topic_alias_proposal, TaskId.directness_tiebreak)

SPECS = {
    task: prompt_registry.register(
        PromptSpec(
            prompt_id=task.value,
            task=task,
            version=1,
            system_text=load_prompt_text(f"{task.value}.v1.md", PROMPT_DIR),
            payload_classes=PAYLOAD,
            changelog="initial topic engine prompt",
        )
    )
    for task in TASKS
}
