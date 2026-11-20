import pytest
from pydantic import BaseModel

from app.core import registry as registries
from app.core.registry import (
    DuplicateRegistrationError,
    PortRegistry,
    Registry,
    SchemaRegistry,
    UnknownRegistrationError,
)


def test_register_and_get() -> None:
    stages: Registry[type] = Registry("stages")

    @stages.register("ingest")
    class Ingest:
        pass

    assert stages.get("ingest") is Ingest
    assert "ingest" in stages
    assert stages.keys() == ["ingest"]
    assert len(stages) == 1


def test_duplicate_name_raises() -> None:
    gates: Registry[str] = Registry("gates")
    gates.add("min_refs", "a")
    with pytest.raises(DuplicateRegistrationError, match="min_refs"):
        gates.add("min_refs", "b")


def test_unknown_name_lists_known_entries() -> None:
    sources: Registry[str] = Registry("sources")
    sources.add("pubmed", "x")
    with pytest.raises(UnknownRegistrationError, match="known: pubmed"):
        sources.get("tavily")


def test_port_registry_scopes_providers_per_port() -> None:
    ports = PortRegistry()
    ports.register("llm", "anthropic")(lambda container: "anthropic-client")
    ports.register("web_search", "anthropic")(lambda container: "other")
    assert ports.get("llm", "anthropic")(None) == "anthropic-client"
    assert sorted(ports.ports()) == ["llm", "web_search"]
    with pytest.raises(UnknownRegistrationError):
        ports.get("llm", "openai_compat")


class ProfileV2(BaseModel):
    name: str
    aliases: list[str]


def test_schema_registry_upcasts_to_current_version() -> None:
    schemas = SchemaRegistry()
    schemas.register("profile", 1, dict)
    schemas.register("profile", 2, ProfileV2)
    schemas.register_upcaster("profile", 1, lambda data: {**data, "aliases": []})
    loaded = schemas.load("profile", 1, {"name": "Apollo"})
    assert loaded == ProfileV2(name="Apollo", aliases=[])
    assert schemas.current_version("profile") == 2


def test_schema_registry_rejects_newer_rows() -> None:
    schemas = SchemaRegistry()
    schemas.register("profile", 1, ProfileV2)
    with pytest.raises(ValueError, match="newer"):
        schemas.upcast("profile", 3, {})


def test_schema_registry_needs_a_complete_chain() -> None:
    schemas = SchemaRegistry()
    schemas.register("evidence", 1, dict)
    schemas.register("evidence", 2, dict)
    with pytest.raises(UnknownRegistrationError, match="no upcaster"):
        schemas.upcast("evidence", 1, {})


@pytest.mark.parametrize(
    "name",
    [
        "stage_registry",
        "recommender_registry",
        "quality_gate_registry",
        "trait_extractor_registry",
        "freshness_source_registry",
        "context_source_registry",
        "redactor_registry",
        "adapter_registry",
        "artifact_schema_registry",
    ],
)
def test_platform_registries_exist(name: str) -> None:
    assert getattr(registries, name) is not None
