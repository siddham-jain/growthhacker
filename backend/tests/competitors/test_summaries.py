from app.ports.llm import TaskId
from app.services.competitors.summaries import grounded
from tests.competitors.apollo import apollo_llm
from tests.competitors.test_golden_apollo import by_name, run_apollo


def test_numbers_must_come_from_the_facts():
    facts = {"uploads_90d": 20, "uploads_matching_topics": 15}
    assert grounded("20 uploads in 90 days; 15 match your topics.", facts)
    assert not grounded("Posts 45 videos a month.", facts)
    assert not grounded("One. Two. Three.", facts)


def summaries_llm(text: str):
    llm = apollo_llm()

    def handler(prompt):
        import json

        facts = json.loads(prompt.trusted[0].text)
        return {"summaries": [{"key": item["key"], "text": text.format(**item)} for item in facts]}

    llm.handlers[TaskId.competitor_evidence_summary] = handler
    return llm


async def test_template_summary_by_default():
    result, *_ = await run_apollo()
    assert "uploads in the last 90 days" in by_name(result, "Fortis Healthcare").evidence_summary


async def test_grounded_llm_summary_is_used():
    result, *_ = await run_apollo(
        llm=summaries_llm("{name} posted {uploads_90d} videos in the last 90 days.")
    )
    assert (
        by_name(result, "Fortis Healthcare").evidence_summary
        == "Fortis Healthcare posted 20 videos in the last 90 days."
    )


async def test_llm_summary_with_invented_numbers_falls_back_to_the_template():
    result, *_ = await run_apollo(llm=summaries_llm("{name} posts 999 videos weekly."))
    assert all("999" not in candidate.evidence_summary for candidate in result.candidates)
