import pytest

from app.services.topics.text.language import NegationDetector, is_code_mixed, is_hinglish
from app.services.topics.text.morphology import english_stem, hindi_stem
from app.services.topics.text.normalize import clean_text, fold, normalize
from app.services.topics.text.phonetic import devanagari_to_latin, latin_skeleton, phonetic_key
from app.services.topics.text.scripts import dominant_script, script_profile
from app.services.topics.text.tokenize import split_hashtag, tokenize


def test_clean_text_strips_zero_width_and_variation_selectors() -> None:
    assert clean_text("knee​ replace‍ment️  cost") == "knee replacement cost"
    assert clean_text("“IVF” ‘cost’") == "\"IVF\" 'cost'"


def test_fold_handles_nukta_chandrabindu_diacritics_and_homoglyphs() -> None:
    assert fold("क़ीमत") == fold("कीमत")
    assert fold("हँसी") == "हंसी"
    assert fold("Café Naïve") == "cafe naive"
    # cyrillic а and е inside a latin word
    assert fold("Knее rеplacеmеnt") == "knee replacement"
    # a fully cyrillic word is left alone
    assert fold("правда") == "правда"


def test_nfkc_folds_compatibility_forms() -> None:
    assert normalize("ＩＶＦ　cost") == "ivf cost"


@pytest.mark.parametrize(
    ("word", "stem"),
    [
        ("replacements", "replac"),
        ("replaced", "replac"),
        ("replacing", "replac"),
        ("knees", "kne"),
        ("surgeries", "surgery"),
    ],
)
def test_english_stem_keys(word: str, stem: str) -> None:
    assert english_stem(word) == stem


def test_hindi_light_stem_folds_oblique_forms() -> None:
    assert hindi_stem("घुटने") == hindi_stem("घुटनों") == hindi_stem("घुटना")
    assert hindi_stem("का") == "का"


def test_tokens_are_whole_words_and_acronyms_keep_case() -> None:
    tokens = tokenize("Heart art IVF IVFX 500mg")
    assert [token.key for token in tokens] == ["heart", "art", "ivf", "ivfx", "500mg"]
    assert tokens[2].is_upper and not tokens[1].is_upper
    assert tokens[0].stem != tokens[1].stem


def test_sentences_split_on_pipes_and_danda() -> None:
    tokens = tokenize("घुटने का दर्द। knee | cost")
    assert tokens[0].sentence < tokens[3].sentence < tokens[4].sentence


@pytest.mark.parametrize(
    ("hashtag", "split"),
    [
        ("#KneeReplacement", "Knee Replacement"),
        ("#kneereplacementsurgery", "knee replacement surgery"),
        ("#घुटने_का_दर्द", "घुटने का दर्द"),
        ("#xyzzyq", "xyzzyq"),
    ],
)
def test_hashtag_splitting(hashtag: str, split: str) -> None:
    assert split_hashtag(hashtag, {"knee", "replacement", "surgery"}) == split


@pytest.mark.parametrize(
    ("text", "script", "skeleton"),
    [
        ("घुटने", "Deva", "ghutne"),
        ("ghutney", "Latn", "ghutne"),
        ("ghootne", "Latn", "ghutne"),
        ("इलाज", "Deva", "ilaj"),
        ("ilaaj", "Latn", "ilaj"),
        ("कितने", "Deva", "kitne"),
    ],
)
def test_phonetic_skeletons_meet_across_scripts(text: str, script: str, skeleton: str) -> None:
    assert phonetic_key(fold(text), script) == skeleton


def test_schwa_deletion_and_folds() -> None:
    assert devanagari_to_latin("प्रत्यारोपण") == "pratyaaropan"
    assert latin_skeleton("phoolwala") == "fulvala"
    assert phonetic_key("cancer", "Taml") is None


def test_script_profile_and_dominant_script() -> None:
    profile = script_profile("घुटने का operation")
    assert set(profile) == {"Deva", "Latn"}
    assert dominant_script(profile) == "Latn"
    assert dominant_script(script_profile("முழங்கால்")) == "Taml"


def test_hinglish_and_code_mixed_detection(lexicons) -> None:
    hinglish = tokenize("Heart attack ke lakshan kya hai")
    assert is_hinglish(hinglish, lexicons)
    assert not is_hinglish(tokenize("Knee replacement cost in India explained by a surgeon"), lexicons)
    assert is_code_mixed({"Latn": 0.5, "Deva": 0.5}, [], lexicons)


@pytest.mark.parametrize(
    ("text", "term_index", "negated"),
    [
        ("this is not a cure for cancer", 5, True),
        ("pathri ka ilaj bina operation ke", 4, True),
        ("यह दवा बिना ऑपरेशन के", 3, True),
        ("cancer care explained", 0, False),
        ("no. cancer screening at 40", 2, False),
    ],
)
def test_negation_window(lexicons, text: str, term_index: int, negated: bool) -> None:
    assert NegationDetector(lexicons, window=3).is_negated(tokenize(text), term_index) is negated
