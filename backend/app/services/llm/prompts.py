import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel

from app.adapters.llm.schema_compiler import compile_schema
from app.core.config import LLMSettings
from app.core.registry import UnknownRegistrationError
from app.ports.llm import PayloadClass, PromptInput, PromptRef, TaskId

PROMPT_DIR = Path(__file__).parent / "prompt_texts"

# returns human-readable problems; aliases that do not exist in the input are the usual case
type SemanticCheck = Callable[[BaseModel, PromptInput], list[str]]
# deterministic input shrinker used once when a provider reports the prompt is too long
type Truncator = Callable[[PromptInput], PromptInput | None]


@dataclass(frozen=True)
class PromptSpec:
    """a static system prompt (no dates, client data or ids) plus its output schema and data policy."""

    prompt_id: str
    task: TaskId
    version: int
    system_text: str
    payload_classes: frozenset[PayloadClass]
    output_schema: type[BaseModel] | None = None
    semantic_check: SemanticCheck | None = None
    truncator: Truncator | None = None
    changelog: str = ""
    examples: str = field(default="", repr=False)

    @cached_property
    def prompt_version(self) -> str:
        schema_json = compile_schema(self.output_schema).canonical_json if self.output_schema else ""
        digest = hashlib.sha256(
            "\x1f".join((self.system_text, self.examples, schema_json)).encode("utf-8")
        ).hexdigest()
        return f"v{self.version}+{digest[:12]}"

    @property
    def full_system_text(self) -> str:
        return f"{self.system_text}\n\n{self.examples}".strip() if self.examples else self.system_text


def load_prompt_text(name: str, directory: Path = PROMPT_DIR) -> str:
    return (directory / name).read_text(encoding="utf-8").strip()


class PromptRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, dict[int, PromptSpec]] = {}

    def register(self, spec: PromptSpec) -> PromptSpec:
        versions = self._specs.setdefault(spec.prompt_id, {})
        if spec.version in versions:
            raise ValueError(f"prompt '{spec.prompt_id}' v{spec.version} is already registered")
        versions[spec.version] = spec
        return spec

    def specs(self) -> list[PromptSpec]:
        return [spec for versions in self._specs.values() for spec in versions.values()]

    def resolve(self, ref: PromptRef, settings: LLMSettings | None = None) -> PromptSpec:
        versions = self._specs.get(ref.prompt_id)
        if not versions:
            known = ", ".join(sorted(self._specs)) or "none"
            raise UnknownRegistrationError(f"prompt '{ref.prompt_id}' is not registered (known: {known})")
        if ref.version != "active":
            number = int(ref.version.removeprefix("v").split("+", 1)[0])
        else:
            pinned = None
            if settings is not None:
                override = settings.tasks.get(next(iter(versions.values())).task.value)
                pinned = override.prompt_version if override is not None else None
            number = pinned if pinned is not None else max(versions)
        try:
            return versions[number]
        except KeyError:
            raise UnknownRegistrationError(f"prompt '{ref.prompt_id}' has no v{number}") from None


prompt_registry = PromptRegistry()
