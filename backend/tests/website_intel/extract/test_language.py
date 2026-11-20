from app.services.website_intel.extract.language import LanguageDetector, dominant_script, normalize_tag

HINDI = "हम जयपुर में पिछले पंद्रह वर्षों से प्रतियोगी परीक्षाओं की तैयारी करवा रहे हैं। " * 4
TAMIL = "சென்னையில் உள்ள எங்கள் மருத்துவமனை சிறந்த சிகிச்சை அளிக்கிறது"
HINGLISH = (
    "Aap ke liye best coaching yahan hai, abhi call karein aur apna seat book karein, fees kitni hai jaaniye"
)
ENGLISH = "Our hospital network offers cardiology, oncology and orthopaedic care across India. " * 4


def test_declared_language_wins():
    assert LanguageDetector().detect(ENGLISH, "en-us") == "en-US"


def test_statistical_detection_for_long_text():
    assert LanguageDetector().detect(HINDI) == "hi"
    assert LanguageDetector().detect(ENGLISH) == "en"


def test_script_detection_for_short_text():
    assert LanguageDetector().detect(TAMIL) == "ta"
    assert dominant_script(TAMIL) == ("ta", "Taml")


def test_hinglish_is_tagged_even_when_declared_english():
    assert LanguageDetector().detect(HINGLISH) == "hi-Latn"
    assert LanguageDetector().detect(HINGLISH, "en") == "hi-Latn"


def test_short_latin_text_without_declaration_is_unknown():
    assert LanguageDetector().detect("Book now") is None


def test_normalize_tag():
    assert normalize_tag("zh_hans_cn") == "zh-Hans-CN"
