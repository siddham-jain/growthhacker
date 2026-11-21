"""importing this package registers every api service implementation with api_service_registry."""

from app.api.services_impl import (  # noqa: F401
    analytics,
    answer_citations,
    channels,
    competitors,
    creators,
    news,
    notifications,
    profile,
    recommendations,
    refresh,
    settings,
    topic_refs,
    trends,
    youtube_oauth,
)
from app.evaluation.ops import api as ops_api  # noqa: F401  (12's OpsService for the /ops routes)
