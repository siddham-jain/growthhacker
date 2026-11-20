from app.services.refresh.evidence import mine_boilerplate
from app.services.refresh.links.urls import add_utm, extract_urls, malformation, normalise_url, same_target
from app.services.refresh.structure import parse_description
from app.services.refresh.text import KeywordPresence, is_distinctive_alias, split_hashtag
from app.services.refresh.triggers.hygiene import hashtag_for
from app.services.refresh.triggers.keywords import stale_years, title_head


def test_keyword_presence_ignores_hyphens_case_plurals_and_hashtag_casing() -> None:
    presence = KeywordPresence("Trying claude FABLE-5.2 with my favourite AI tool. #DiabeticFootCare")
    assert presence.contains("Claude Fable 5.2")
    assert presence.contains("AI tools")
    assert presence.contains("diabetic foot care")
    assert not presence.contains("Claude Fable 5.1")
    assert KeywordPresence("Visit our café").contains("cafe")


def test_version_numbers_stay_distinct() -> None:
    assert not KeywordPresence("Claude Fable 5.21 is out").contains("Claude Fable 5.2")
    assert KeywordPresence("Claude Fable 5.2 and 5.21").count("Claude Fable 5.2") == 1


def test_single_common_word_aliases_are_ignored() -> None:
    assert not is_distinctive_alias("Fable")
    assert is_distinctive_alias("Fable 5.2")
    assert is_distinctive_alias("Claude Fable")


def test_hashtag_helpers() -> None:
    assert split_hashtag("#DiabeticFootCare") == "Diabetic Foot Care"
    assert hashtag_for("AI tools") == "#AITools"
    assert hashtag_for("diabetic foot care") == "#DiabeticFootCare"
    assert hashtag_for("a very long phrase that cannot possibly fit") is None


def test_parse_description_structure() -> None:
    text = (
        "Diabetic foot care basics.\n"
        "Daily checks matter.\n"
        "\n"
        "00:00 Intro\n"
        "00:45 Daily checks\n"
        "02:10 Warning signs\n"
        "\n"
        "__________\n"
        "Facebook—https://www.facebook.com/TheApolloHospitals/.\n"
        "#Diabetes #FootCare"
    )
    structure = parse_description(text)
    assert structure.first_paragraph_end_line == 1
    assert structure.separator_lines == (7,)
    assert structure.links_block_start_line == 7
    assert structure.hashtag_line == 9
    assert structure.chapters_valid
    assert [url.raw for url in structure.urls] == ["https://www.facebook.com/TheApolloHospitals/"]
    assert structure.urls[0].line_index == 8
    assert structure.hashtags == ("#Diabetes", "#FootCare")


def test_invalid_chapters_are_explained() -> None:
    structure = parse_description("Intro\n\n00:10 Start\n00:15 Middle\n")
    assert not structure.chapters_valid
    assert "the first timestamp must be 00:00" in structure.chapter_problems
    assert "you need at least 3 timestamps" in structure.chapter_problems


def test_crlf_and_multibyte_lengths() -> None:
    structure = parse_description("मधुमेह देखभाल\r\nदूसरी पंक्ति 🙂")
    assert structure.newline == "\r\n"
    assert structure.lines == ("मधुमेह देखभाल", "दूसरी पंक्ति 🙂")
    assert structure.byte_length > structure.char_length * 2


def test_boilerplate_is_mined_from_shared_lines() -> None:
    footer = "Follow us: https://x.com/apollo?utm_source=a\nApollo Hospitals is the leading provider."
    descriptions = [f"Video {index} summary.\n{footer}" for index in range(5)] + ["Something else"]
    keys = mine_boilerplate(descriptions, min_share=0.3, min_descriptions=3)
    structure = parse_description(descriptions[0], keys)
    assert structure.boilerplate_lines == frozenset({1, 2})
    assert structure.links_block_start_line == 1


def test_malformed_urls_and_repairs() -> None:
    apollo = malformation("https://www.facebook.com/https://www.facebook.com/TheApolloHospitals/")
    assert apollo is not None and apollo.repaired == "https://www.facebook.com/TheApolloHospitals/"
    doubled = malformation("https://facebook.com/facebook.com/Apollo")
    assert doubled is not None and doubled.repaired == "https://facebook.com/Apollo"
    foreign = malformation("https://bit.ly/https://example.com/page")
    assert foreign is not None and foreign.repaired is None
    assert malformation("https://localhost/path") is not None
    assert malformation("https://www.apollohospitals.com/book/") is None


def test_url_normalisation_ignores_utm_scheme_www_and_slashes() -> None:
    assert normalise_url("http://www.Example.com/book/?utm_source=yt&a=1") == "example.com/book?a=1"
    assert same_target("https://example.com/book?utm_campaign=x", "http://www.example.com/book/")
    assert same_target("https://example.com/book/cardiology", "https://example.com/book")
    assert not same_target("https://example.com/booking", "https://example.com/book")
    assert add_utm("https://example.com/book?utm_source=x", "vid") == "https://example.com/book?utm_source=x"
    tagged = add_utm("https://example.com/book#top", "abc")
    assert tagged.endswith("utm_campaign=abc#top")


def test_url_extraction_trims_trailing_punctuation() -> None:
    urls = extract_urls("See (https://example.com/a), and https://example.com/b.")
    assert [url.raw for url in urls] == ["https://example.com/a", "https://example.com/b"]


def test_stale_year_patterns_skip_dated_context() -> None:
    assert stale_years("Best AI tools in 2025", 2026) == [2025]
    assert stale_years("2025 guide to knee care", 2026) == [2025]
    assert stale_years("Live on 24 Nov 2025", 2026) == []
    assert stale_years("Serving patients since 2019", 2026) == []
    assert stale_years("Results from 2019-2024", 2026) == []
    assert stale_years("2018 Kerala floods relief", 2026) == []
    assert stale_years("Plans for 2026", 2026) == []


def test_title_head() -> None:
    assert title_head("Heart Health | Apollo Hospitals Chennai") == "Heart Health"
    assert title_head("How to make money using AI in 2026") == "How to make money using AI in 2026"
