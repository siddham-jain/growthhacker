import re

from app.domain.profile import FreshnessSensitivity, RefreshPolicy

# 03 owns the IndustryCode taxonomy (99 G14); codes outside it collapse to "other"
INDUSTRY_PRIORS: dict[str, float] = {
    "ai_software": 0.90,
    "consumer_electronics": 0.80,
    "crypto_fintech": 0.80,
    "b2b_saas": 0.65,
    "pharma": 0.60,
    "healthcare_hospital": 0.55,
    "healthcare_clinic": 0.45,
    "financial_services": 0.55,
    "media_publisher": 0.60,
    "education_coaching": 0.45,
    "auto_dealer": 0.45,
    "travel": 0.45,
    "legal_services": 0.35,
    "ecommerce_fashion": 0.40,
    "real_estate": 0.35,
    "manufacturing": 0.35,
    "nonprofit": 0.30,
    "home_services": 0.20,
    "healthcare_dental": 0.20,
    "restaurant": 0.15,
    "other": 0.40,
}
DEFAULT_PRIOR = INDUSTRY_PRIORS["other"]
KEYWORD_INDUSTRY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"dental|dentist|orthodont", re.I), "healthcare_dental"),
    (re.compile(r"hospital|multi-?speciality|healthcare", re.I), "healthcare_hospital"),
    (re.compile(r"clinic|physiotherap|dermatolog", re.I), "healthcare_clinic"),
    (re.compile(r"pharma", re.I), "pharma"),
    (re.compile(r"artificial intelligence|\bai\b|machine learning", re.I), "ai_software"),
    (re.compile(r"software|saas", re.I), "b2b_saas"),
    (re.compile(r"car dealer|automobile dealer|auto dealer", re.I), "auto_dealer"),
    (re.compile(r"coaching|tuition|education", re.I), "education_coaching"),
    (re.compile(r"real estate|property", re.I), "real_estate"),
    (re.compile(r"restaurant|cafe|food", re.I), "restaurant"),
    (re.compile(r"plumb|electrician|cleaning|pest control|home services", re.I), "home_services"),
    (re.compile(r"bank|insurance|financial", re.I), "financial_services"),
)
REGULATED_INDUSTRIES: dict[str, str] = {
    "healthcare_hospital": "healthcare",
    "healthcare_clinic": "healthcare",
    "healthcare_dental": "healthcare",
    "pharma": "healthcare",
    "financial_services": "finance",
    "crypto_fintech": "finance",
    "legal_services": "legal",
}
# 99 C25: news cadence 6/6/24/168/0 by level
REFRESH_POLICIES: dict[FreshnessSensitivity, RefreshPolicy] = {
    FreshnessSensitivity.very_high: RefreshPolicy(
        news_scan_interval_hours=6,
        refresh_check_interval_days=7,
        description_refresh_eligible=True,
        min_signal_strength=0.5,
    ),
    FreshnessSensitivity.high: RefreshPolicy(
        news_scan_interval_hours=6,
        refresh_check_interval_days=14,
        description_refresh_eligible=True,
        min_signal_strength=0.6,
    ),
    FreshnessSensitivity.medium: RefreshPolicy(
        news_scan_interval_hours=24,
        refresh_check_interval_days=30,
        description_refresh_eligible=True,
        min_signal_strength=0.7,
    ),
    FreshnessSensitivity.low: RefreshPolicy(
        news_scan_interval_hours=168,
        refresh_check_interval_days=90,
        description_refresh_eligible=True,
        min_signal_strength=0.8,
    ),
    FreshnessSensitivity.very_low: RefreshPolicy(
        news_scan_interval_hours=0,
        refresh_check_interval_days=180,
        description_refresh_eligible=False,
        min_signal_strength=0.9,
    ),
}


def normalise_industry(code: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", code.strip().lower()).strip("_")
    return cleaned if cleaned in INDUSTRY_PRIORS else "other"


def industry_from_text(text: str) -> str:
    for pattern, code in KEYWORD_INDUSTRY:
        if pattern.search(text):
            return code
    return "other"


def bucket(score: float) -> FreshnessSensitivity:
    if score < 0.2:
        return FreshnessSensitivity.very_low
    if score < 0.4:
        return FreshnessSensitivity.low
    if score < 0.6:
        return FreshnessSensitivity.medium
    if score < 0.8:
        return FreshnessSensitivity.high
    return FreshnessSensitivity.very_high
