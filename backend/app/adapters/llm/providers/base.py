from typing import Any, Protocol

from app.adapters.llm.capabilities import ModelCapabilities
from app.adapters.llm.types import ProviderRequest, ProviderResponse


class ChatProvider(Protocol):
    # a new backend (bedrock, vertex, a local server) is a new class; the fallback chain never changes
    name: str

    def capabilities(self, model: str) -> ModelCapabilities: ...

    def schema_in_prompt(self) -> bool: ...

    # transport and http errors raise LLMError; refusals, truncation and empty text come back as responses
    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...

    def extract_json(self, text: str) -> Any: ...

    async def aclose(self) -> None: ...
