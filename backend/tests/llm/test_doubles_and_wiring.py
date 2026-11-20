import pytest

from app.adapters.llm.client import DefaultLLMClient
from app.adapters.llm.factory import build_providers
from app.adapters.llm.testing import FakeLLMClient, RecordingLLMClient, ReplayLLMClient
from app.core.container import build_container
from app.ports.llm import (
    CallContext,
    LLMError,
    LLMErrorKind,
    LLMUnavailable,
    PayloadClass,
    PromptInput,
    TaskId,
    TrustedBlock,
)
from app.services.llm.prompts import prompt_registry
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.specs import CARD_WRITE_PROMPT
from tests.conftest import make_settings
from tests.llm.factories import CLIENT_ID, GOOD_COPY, RUN_ID, make_llm_settings

CONTEXT = CallContext(
    run_id=RUN_ID, client_id=CLIENT_ID, stage="test", payload_classes=frozenset({PayloadClass.public_youtube})
)
INPUT = PromptInput(trusted=[TrustedBlock(label="evidence_pack", text="{}")])


async def test_fake_client_scripts_replies_and_errors() -> None:
    fake = FakeLLMClient({TaskId.card_write: [GOOD_COPY, LLMError(LLMErrorKind.refusal, "bio")]})
    result = await fake.generate_structured(
        task=TaskId.card_write, schema=CardCopyLLM, system=CARD_WRITE_PROMPT, input=INPUT, context=CONTEXT
    )
    assert isinstance(result.value, CardCopyLLM)
    with pytest.raises(LLMUnavailable) as raised:
        await fake.generate_structured(
            task=TaskId.card_write, schema=CardCopyLLM, system=CARD_WRITE_PROMPT, input=INPUT, context=CONTEXT
        )
    assert raised.value.kind is LLMErrorKind.refusal
    assert len(fake.calls_for(TaskId.card_write)) == 2


async def test_recorded_fixtures_replay_and_misses_fail_loudly(tmp_path) -> None:
    class Versioned(FakeLLMClient):
        def _result(self, call, value, raw_text):
            result = super()._result(call, value, raw_text)
            return result.model_copy(
                update={"prompt_version": prompt_registry.resolve(call.system).prompt_version}
            )

    recorder = RecordingLLMClient(Versioned({TaskId.card_write: GOOD_COPY}), tmp_path)
    await recorder.generate_structured(
        task=TaskId.card_write, schema=CardCopyLLM, system=CARD_WRITE_PROMPT, input=INPUT, context=CONTEXT
    )
    replay = ReplayLLMClient(tmp_path, prompt_registry)
    result = await replay.generate_structured(
        task=TaskId.card_write, schema=CardCopyLLM, system=CARD_WRITE_PROMPT, input=INPUT, context=CONTEXT
    )
    assert result.value == CardCopyLLM.model_validate(GOOD_COPY)
    other = PromptInput(trusted=[TrustedBlock(label="evidence_pack", text="different")])
    with pytest.raises(LookupError, match="re-record"):
        await replay.generate_structured(
            task=TaskId.card_write, schema=CardCopyLLM, system=CARD_WRITE_PROMPT, input=other, context=CONTEXT
        )


def test_providers_are_built_only_when_configured() -> None:
    assert set(build_providers(make_llm_settings())) == {"anthropic", "openai_compat"}
    assert set(build_providers(make_llm_settings(openai_compat={}))) == {"anthropic"}
    assert build_providers(make_llm_settings(anthropic={}, openai_compat={})) == {}


async def test_container_resolves_the_registered_llm_adapter() -> None:
    settings = make_settings(llm={"anthropic": {"api_key": "test-key"}})
    container = build_container(settings)
    try:
        client = container.adapter("llm")
        assert isinstance(client, DefaultLLMClient)
    finally:
        await container.aclose()


async def test_unconfigured_deploy_degrades_instead_of_crashing() -> None:
    container = build_container(make_settings())
    try:
        client = container.adapter("llm")
        with pytest.raises(LLMUnavailable) as raised:
            await client.generate_structured(
                task=TaskId.card_write,
                schema=CardCopyLLM,
                system=CARD_WRITE_PROMPT,
                input=INPUT,
                context=CONTEXT,
            )
        assert raised.value.kind is LLMErrorKind.disabled
    finally:
        await container.aclose()
