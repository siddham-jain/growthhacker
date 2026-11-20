from collections.abc import Mapping, Sequence

from app.domain.card_copy import CardCopy
from app.ports.llm import LLMError, LLMErrorKind
from app.services.recommend.writer import CardRequest, TemplateCardRenderer
from tests.recommend.fixtures import GENERATED_AT, RUN_ID, SPECS

SLOP = {
    "summary": "Guaranteed viral knee video: 9.9x views",
    "fit_to_client": "Fortis earns huge ad revenue from this; viewers are mostly women",
}


class StubWriter:
    """an llm stand-in that writes from the template, then lets a test corrupt it."""

    def __init__(self, corrupt=None, rewrite=None, fail: Exception | None = None) -> None:
        self.corrupt = corrupt or (lambda copy, request: copy)
        self.rewrite = rewrite
        self.fail = fail
        self.calls: list[list[CardRequest]] = []

    async def write(self, requests: Sequence[CardRequest]) -> Mapping[str, CardCopy]:
        self.calls.append(list(requests))
        if self.fail is not None:
            raise self.fail
        result = {}
        for request in requests:
            base = (
                TemplateCardRenderer()
                .render(request.draft, request.client)
                .model_copy(update={"copy_source": "llm", "prompt_id": "card_write", "attempts": 1})
            )
            change = self.rewrite if request.violations and self.rewrite else self.corrupt
            result[request.draft.draft_id] = change(base, request)
        return result


async def generate(service, inputs, writer):
    return await service.generate(inputs, run_id=RUN_ID, generated_at=GENERATED_AT, writer=writer)


def selected_cards(run):
    return run.output.window(run.output.selected_window).set.recommendations


async def test_clean_llm_copy_is_used_for_the_selected_window_only(service, inputs) -> None:
    writer = StubWriter()
    run = await generate(service, inputs, writer)

    assert {card.extras.rendered_by for card in selected_cards(run)} == {"llm"}
    assert {card.extras.rendered_by for card in run.output.window(90).set.recommendations} == {"template"}
    assert run.writer_fallbacks == 0


async def test_violations_get_one_rewrite_with_the_reasons_listed(service, inputs) -> None:
    def invent(copy, request):
        return copy.model_copy(update={"summary": copy.summary[:100] + " (7.7x)"})

    writer = StubWriter(corrupt=invent, rewrite=lambda copy, request: copy)
    run = await generate(service, inputs, writer)

    rewrites = writer.calls[1]
    assert rewrites and all(request.violations for request in rewrites)
    assert {violation.detail for request in rewrites for violation in request.violations} == {
        "number_not_in_evidence"
    }
    assert {card.extras.rendered_by for card in selected_cards(run)} == {"llm"}


async def test_persistent_slop_falls_back_to_the_template_and_never_ships(service, inputs) -> None:
    def slop(copy, request):
        return copy.model_copy(update=SLOP)

    run = await generate(service, inputs, StubWriter(corrupt=slop))
    cards = selected_cards(run)

    assert cards and {card.extras.rendered_by for card in cards} == {"template"}
    assert run.writer_fallbacks >= len(cards)
    for card in cards:
        text = " ".join([card.summary, card.fit_to_client, *card.why_it_works]).lower()
        assert not [
            phrase for phrase in ("guaranteed", "viral", "revenue", "9.9x", "mostly women") if phrase in text
        ]


async def test_copied_competitor_titles_never_reach_a_card(service, inputs) -> None:
    def copy_title(copy, request):
        return copy.model_copy(update={"summary": SPECS["k1"].title, "working_title": SPECS["k3"].title})

    run = await generate(service, inputs, StubWriter(corrupt=copy_title))
    titles = {spec.title for spec in SPECS.values()}

    assert all(card.summary not in titles for card in selected_cards(run))
    assert all(not set(card.extras.example_titles) & titles for card in selected_cards(run))


async def test_writer_outage_degrades_to_template_without_blocking(service, inputs) -> None:
    writer = StubWriter(fail=LLMError(LLMErrorKind.overloaded, "provider overloaded"))
    run = await generate(service, inputs, writer)

    cards = selected_cards(run)
    assert cards and {card.extras.rendered_by for card in cards} == {"template"}
    assert run.writer_fallbacks >= len(cards)


async def test_unknown_reason_ids_from_the_writer_are_rejected(service, inputs) -> None:
    def extra_reason(copy, request):
        return copy.model_copy(update={"reason_fact_ids": [["r1", "r7"]] + copy.reason_fact_ids[1:]})

    run = await generate(service, inputs, StubWriter(corrupt=extra_reason))

    assert {card.extras.rendered_by for card in selected_cards(run)} == {"template"}
