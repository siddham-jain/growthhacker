# importing this module registers every candidate generator, resolver, ownership signal and drift rule
from app.services.competitors import prompt_specs  # noqa: F401
from app.services.competitors.drift import rules  # noqa: F401
from app.services.competitors.resolve import (  # noqa: F401
    cached,
    legacy_and_handle,
    site_link,
    websearch_youtube,
    youtube_search,
)
from app.services.competitors.score import business  # noqa: F401
from app.services.competitors.sources import (  # noqa: F401
    llm_source,
    news_source,
    niche_search_source,
    user_source,
    web_search_source,
    website_hint_source,
)
from app.services.competitors.verify import signals  # noqa: F401
