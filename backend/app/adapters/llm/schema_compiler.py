import json
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

from app.adapters.llm.types import CompiledSchema

# enforced client-side by pydantic; providers reject or ignore them on the wire
STRIPPED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "default",
        "title",
        "examples",
    }
)
KEPT_FORMATS = frozenset({"date", "date-time"})
MAX_PROPERTIES = 100
MAX_NESTING = 5
MAX_ENUM_VALUES = 1000


class SchemaLintError(ValueError):
    pass


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _check_recursion(defs: dict[str, Any]) -> None:
    def refs_in(node: Any) -> set[str]:
        if isinstance(node, dict):
            found = {_ref_name(node["$ref"])} if "$ref" in node else set()
            for value in node.values():
                found |= refs_in(value)
            return found
        if isinstance(node, list):
            return set().union(*(refs_in(item) for item in node)) if node else set()
        return set()

    graph = {name: refs_in(body) for name, body in defs.items()}

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            raise SchemaLintError(f"recursive schema through {' -> '.join((*path, name))}")
        for child in graph.get(name, ()):
            visit(child, (*path, name))

    for name in graph:
        visit(name, ())


def _transform(node: Any) -> Any:
    if isinstance(node, list):
        return [_transform(item) for item in node]
    if not isinstance(node, dict):
        return node
    if isinstance(node.get("additionalProperties"), dict):
        raise SchemaLintError(
            "free-form mappings are not allowed in structured outputs; use a list of objects"
        )
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in STRIPPED_KEYWORDS:
            continue
        if key == "format" and value not in KEPT_FORMATS:
            continue
        if key == "properties":
            out[key] = {name: _transform(body) for name, body in value.items()}
        else:
            out[key] = _transform(value)
    if out.get("type") == "object" or "properties" in out:
        out["type"] = "object"
        out["additionalProperties"] = False
        out["required"] = sorted(out.get("properties", {}))
        out.setdefault("properties", {})
    return out


def _lint(schema: dict[str, Any], defs: dict[str, Any]) -> None:
    counters = {"properties": 0, "enum": 0}

    def walk(node: Any, depth: int, seen: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, depth, seen)
            return
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            name = _ref_name(node["$ref"])
            walk(defs.get(name, {}), depth, (*seen, name))
        if "enum" in node:
            counters["enum"] += len(node["enum"])
        if "properties" in node:
            depth += 1
            if depth > MAX_NESTING:
                raise SchemaLintError(f"schema nests deeper than {MAX_NESTING} object levels")
            counters["properties"] += len(node["properties"])
            for body in node["properties"].values():
                walk(body, depth, seen)
        for key in ("items", "anyOf", "allOf"):
            if key in node:
                walk(node[key], depth, seen)

    walk(schema, 0, ())
    if counters["properties"] > MAX_PROPERTIES:
        raise SchemaLintError(f"schema has {counters['properties']} properties (max {MAX_PROPERTIES})")
    if counters["enum"] > MAX_ENUM_VALUES:
        raise SchemaLintError(f"schema has {counters['enum']} enum values (max {MAX_ENUM_VALUES})")


@lru_cache(maxsize=256)
def compile_schema(model: type[BaseModel]) -> CompiledSchema:
    raw = model.model_json_schema(mode="validation")
    defs = raw.pop("$defs", {})
    _check_recursion(defs)
    if raw.get("type") != "object" or "anyOf" in raw:
        raise SchemaLintError(f"{model.__name__}: the root schema must be a plain object")
    schema = _transform(raw)
    if defs:
        schema["$defs"] = {name: _transform(body) for name, body in sorted(defs.items())}
    _lint(schema, schema.get("$defs", {}))
    canonical = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return CompiledSchema(name=model.__name__, schema=json.loads(canonical), canonical_json=canonical)
