from collections.abc import Sequence

from app.adapters.youtube.errors import YouTubeApiError
from app.core.logging import get_logger
from app.domain.youtube import QuotaPriority
from app.ports.quota import QuotaExhausted
from app.ports.youtube import YouTubeGateway
from app.services.youtube.context import request_context

logger = get_logger(__name__)


class YouTubeCacheRefresher:
    """re-fetches still-referenced cache rows before the 28-day act-by age; misses age out via retention."""

    def __init__(self, gateway: YouTubeGateway) -> None:
        self._gateway = gateway

    async def schedule(self, table: str, ids: Sequence[str]) -> None:
        ctx = request_context(QuotaPriority.background, max_age_s=0)
        try:
            if table == "yt_channels":
                await self._gateway.channels_by_id(ids, ctx)
            elif table == "yt_videos":
                await self._gateway.videos_by_id(ids, ctx)
            else:
                raise ValueError(f"no youtube refresh for table {table}")
        except (QuotaExhausted, YouTubeApiError) as error:
            # unrefreshed rows are deleted by the sweeper's fail-closed pass
            logger.warning("youtube_refresh_skipped", table=table, ids=len(ids), error=type(error).__name__)
