from collections.abc import Callable, Iterator
from typing import Any


class DuplicateRegistrationError(ValueError):
    pass


class UnknownRegistrationError(KeyError):
    pass


class Registry[T]:
    def __init__(self, name: str) -> None:
        self.name = name
        self._entries: dict[str, T] = {}

    def add(self, key: str, value: T) -> T:
        if key in self._entries:
            raise DuplicateRegistrationError(f"{self.name}: '{key}' is already registered")
        self._entries[key] = value
        return value

    def register(self, key: str) -> Callable[[T], T]:
        def decorator(value: T) -> T:
            return self.add(key, value)

        return decorator

    def get(self, key: str) -> T:
        try:
            return self._entries[key]
        except KeyError:
            known = ", ".join(sorted(self._entries)) or "none"
            raise UnknownRegistrationError(
                f"{self.name}: '{key}' is not registered (known: {known})"
            ) from None

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def keys(self) -> list[str]:
        return list(self._entries)

    def all(self) -> dict[str, T]:
        return dict(self._entries)


class PortRegistry:
    def __init__(self) -> None:
        self._ports: dict[str, Registry[Callable[..., Any]]] = {}

    def port(self, port_name: str) -> Registry[Callable[..., Any]]:
        if port_name not in self._ports:
            self._ports[port_name] = Registry(f"adapter_registry[{port_name}]")
        return self._ports[port_name]

    def register(self, port_name: str, provider: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self.port(port_name).register(provider)

    def get(self, port_name: str, provider: str) -> Callable[..., Any]:
        return self.port(port_name).get(provider)

    def ports(self) -> list[str]:
        return list(self._ports)


class SchemaRegistry:
    """(kind, version) -> pydantic model, plus upcasters from version n to n + 1."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, int], type] = {}
        self._upcasters: dict[tuple[str, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def register(self, kind: str, version: int, model: type) -> None:
        if (kind, version) in self._models:
            raise DuplicateRegistrationError(
                f"artifact_schema_registry: '{kind}' v{version} is already registered"
            )
        self._models[(kind, version)] = model

    def register_upcaster(
        self, kind: str, from_version: int, upcaster: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        if (kind, from_version) in self._upcasters:
            raise DuplicateRegistrationError(
                f"artifact_schema_registry: upcaster {kind} v{from_version} exists"
            )
        self._upcasters[(kind, from_version)] = upcaster

    def current_version(self, kind: str) -> int:
        versions = [version for registered_kind, version in self._models if registered_kind == kind]
        if not versions:
            raise UnknownRegistrationError(f"artifact_schema_registry: '{kind}' is not registered")
        return max(versions)

    def model(self, kind: str, version: int | None = None) -> type:
        version = self.current_version(kind) if version is None else version
        try:
            return self._models[(kind, version)]
        except KeyError:
            raise UnknownRegistrationError(
                f"artifact_schema_registry: '{kind}' v{version} is not registered"
            ) from None

    def upcast(self, kind: str, version: int, data: dict[str, Any]) -> dict[str, Any]:
        current = self.current_version(kind)
        if version > current:
            raise ValueError(f"'{kind}' v{version} is newer than this build knows (v{current})")
        while version < current:
            try:
                data = self._upcasters[(kind, version)](data)
            except KeyError:
                raise UnknownRegistrationError(f"no upcaster for '{kind}' v{version}") from None
            version += 1
        return data

    def load(self, kind: str, version: int, data: dict[str, Any]) -> Any:
        return self.model(kind).model_validate(self.upcast(kind, version, data))


stage_registry: Registry[Any] = Registry("stage_registry")
recommender_registry: Registry[Any] = Registry("recommender_registry")
quality_gate_registry: Registry[Any] = Registry("quality_gate_registry")
trait_extractor_registry: Registry[Any] = Registry("trait_extractor_registry")
freshness_source_registry: Registry[Any] = Registry("freshness_source_registry")
context_source_registry: Registry[Any] = Registry("context_source_registry")
redactor_registry: Registry[Callable[..., Any]] = Registry("redactor_registry")
adapter_registry = PortRegistry()
artifact_schema_registry = SchemaRegistry()
