from app.domain.profile import BusinessProfile

MAX_BUYER_QUESTIONS = 10


def top_offering(profile: BusinessProfile) -> str | None:
    ranked = sorted(profile.offerings, key=lambda offering: (not offering.is_primary, offering.priority))
    return ranked[0].name if ranked else None


def category(profile: BusinessProfile) -> str:
    return profile.identity.niche or profile.identity.industry.replace("_", " ")


def profile_brief(profile: BusinessProfile, buyer_questions: list[str]) -> str:
    # compact and free of crawled html; this is the only client context the llm sees
    identity = profile.identity
    lines = [
        f"Brand: {identity.name}",
        f"Aliases: {', '.join(identity.brand_aliases) or 'none'}",
        f"Website: {identity.website.registrable_domain if identity.website else 'unknown'}",
        f"Category: {category(profile)}",
        f"Business model: {identity.business_model.value}",
        f"Geographic scope: {identity.geo_scope}",
        f"Primary markets: {', '.join(identity.primary_markets) or identity.country or 'unknown'}",
        f"Cities: {', '.join(profile.locations.cities[:15]) or 'unknown'}",
        f"Group brands (not competitors): {', '.join(brand.name for brand in identity.related_brands) or 'none'}",
        "Offerings:",
        *(f"- [{offering.id}] {offering.name}" for offering in profile.offerings[:25]),
        "Value propositions:",
        *(f"- {value.statement}" for value in profile.value_propositions[:8]),
        "Buyer questions:",
        *(f"- {question}" for question in buyer_questions[:MAX_BUYER_QUESTIONS]),
    ]
    return "\n".join(lines)
