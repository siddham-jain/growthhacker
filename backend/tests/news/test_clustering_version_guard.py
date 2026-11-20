from datetime import timedelta

import numpy as np
import pytest

from app.services.news.grounding import GroundingValidator
from app.services.news.judge import AliasMap
from app.services.news.versions import version_tokens, versions_conflict
from tests.news.fixtures import NOW, bundles, clusterer, entity, judgement, news_item


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Anthropic releases Claude Fable 5.1", "Anthropic releases Claude Fable 5.2"),
        ("OpenAI ships GPT-5", "OpenAI ships GPT-5.1"),
        ("Apple iPhone 17 review", "Apple iPhone 17 Pro review"),
    ],
)
def test_version_tokens_disagree_for_adjacent_versions(left, right):
    assert versions_conflict(version_tokens(left), version_tokens(right))


def test_plain_numbers_and_years_are_not_versions():
    assert version_tokens("Top 10 AI tools in 2026") == frozenset()
    assert version_tokens("Startup raises $5.2 million") == frozenset()
    assert version_tokens("Llama 4 beats 3 rivals") == {"4"}


def test_fable_5_1_and_5_2_stay_separate_stories_while_5_2_coverage_merges():
    items = [
        news_item(
            "https://www.anthropic.com/news/claude-fable-5-1",
            "Introducing Claude Fable 5.1",
            "",
            NOW - timedelta(days=2),
        ),
        news_item(
            "https://techcrunch.com/fable-5-1",
            "Anthropic releases Claude Fable 5.1",
            "",
            NOW - timedelta(days=2),
        ),
        news_item(
            "https://www.anthropic.com/news/claude-fable-5-2",
            "Introducing Claude Fable 5.2",
            "",
            NOW - timedelta(hours=14),
        ),
        news_item(
            "https://techcrunch.com/fable-5-2",
            "Anthropic releases Claude Fable 5.2",
            "",
            NOW - timedelta(hours=12),
        ),
        news_item(
            "https://www.theverge.com/fable-5-2",
            "Anthropic's Claude Fable 5.2 is here",
            "",
            NOW - timedelta(hours=11),
        ),
    ]
    stories = {tuple(sorted(s.version_tokens)): s for s in (b.story for b in bundles(items))}
    assert set(stories) == {("5.1",), ("5.2",)}
    assert stories[("5.2",)].outlet_count == 3
    assert stories[("5.1",)].outlet_count == 2


def test_version_guard_blocks_even_identical_looking_titles_and_close_embeddings():
    first = news_item("https://a.example/1", "Claude Fable 5.1 benchmarks", "", NOW - timedelta(hours=3))
    second = news_item("https://b.example/2", "Claude Fable 5.2 benchmarks", "", NOW - timedelta(hours=2))
    vector = np.ones(4, dtype=np.float32) / 2
    result = clusterer().cluster([first, second], NOW, {first.id: vector, second.id: vector})
    assert len(result.stories) == 2


def test_exact_url_and_doi_duplicates_collapse():
    first = news_item("https://www.example.com/story?utm_source=x", "Story one", "", NOW - timedelta(hours=3))
    second = news_item("https://example.com/story/", "Story one (updated)", "", NOW - timedelta(hours=2))
    result = clusterer().cluster([first, second], NOW)
    assert first.url_hash == second.url_hash
    assert len(result.stories) == 1


def test_grounding_never_canonicalises_fable_5_2_to_a_versionless_name():
    item = news_item(
        "https://techcrunch.com/x", "Anthropic releases Claude Fable 5.2", "Claude Fable 5.2 is out.", NOW
    )
    aliases = AliasMap(items={"n1": item}, offerings={"o1": "off_slides"}, value_props={"v1": "vp_models"})
    from tests.news.fixtures import runable_profile

    watch = runable_profile().freshness.watch_entities
    result = GroundingValidator().validate(judgement(entity("Claude Fable 5.2", "n1")), aliases, watch)
    assert result.judgement.key_entity.canonical == "Claude Fable 5.2"
    invented = GroundingValidator().validate(judgement(entity("Claude Fable 5.3", "n1")), aliases, watch)
    assert invented.judgement is None and invented.reason == "ungrounded_entity"
