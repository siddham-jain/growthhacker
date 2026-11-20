import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.adapters.llm.types import CompiledSchema
from app.core.registry import Registry


class StructuredDialect(Protocol):
    name: str
    # when true the renderer appends the schema text to the system prompt
    schema_in_prompt: bool

    def response_format(self, schema: CompiledSchema) -> dict[str, Any] | None: ...

    def extract(self, text: str) -> Any: ...


def first_json_object(text: str) -> str:
    # first balanced {...} span, ignoring braces inside strings
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for position in range(start, len(text)):
            char = text[position]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : position + 1]
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in the response")


def schema_instruction(schema: CompiledSchema) -> str:
    return (
        "Respond with a single JSON object and nothing else. It must validate against this JSON schema:\n"
        f"{schema.canonical_json}"
    )


structured_dialects: Registry[StructuredDialect] = Registry("llm_structured_dialects")


@dataclass(frozen=True, slots=True)
class JsonSchemaDialect:
    name: str
    strict: bool
    schema_in_prompt: bool = False

    def response_format(self, schema: CompiledSchema) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": schema.name, "schema": dict(schema.schema)}
        if self.strict:
            payload["strict"] = True
        return {"type": "json_schema", "json_schema": payload}

    def extract(self, text: str) -> Any:
        return json.loads(text)


@dataclass(frozen=True, slots=True)
class JsonObjectDialect:
    name: str = "openai_json_object"
    schema_in_prompt: bool = True

    def response_format(self, schema: CompiledSchema) -> dict[str, Any]:
        return {"type": "json_object"}

    def extract(self, text: str) -> Any:
        return json.loads(text)


@dataclass(frozen=True, slots=True)
class PromptOnlyDialect:
    name: str = "prompt_only"
    schema_in_prompt: bool = True

    def response_format(self, schema: CompiledSchema) -> None:
        return None

    def extract(self, text: str) -> Any:
        return json.loads(first_json_object(text))


structured_dialects.add(
    "openai_json_schema_strict", JsonSchemaDialect(name="openai_json_schema_strict", strict=True)
)
structured_dialects.add("openai_json_schema", JsonSchemaDialect(name="openai_json_schema", strict=False))
structured_dialects.add("openai_json_object", JsonObjectDialect())
structured_dialects.add("prompt_only", PromptOnlyDialect())
