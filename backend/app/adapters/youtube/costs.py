from dataclasses import dataclass

from app.core.registry import Registry
from app.domain.youtube import QuotaBucket


@dataclass(frozen=True)
class EndpointCost:
    bucket: QuotaBucket
    units: int


ENDPOINT_COSTS: Registry[EndpointCost] = Registry("youtube_endpoint_costs")

for _endpoint, _bucket, _units in (
    ("channels.list", QuotaBucket.main, 1),
    ("playlistItems.list", QuotaBucket.main, 1),
    ("playlists.list", QuotaBucket.main, 1),
    ("videos.list", QuotaBucket.main, 1),
    ("commentThreads.list", QuotaBucket.main, 1),
    ("videos.batchGetStats", QuotaBucket.batch_stats, 1),
    ("search.list", QuotaBucket.search, 1),
    ("analytics.reports.query", QuotaBucket.analytics, 1),
):
    ENDPOINT_COSTS.add(_endpoint, EndpointCost(bucket=_bucket, units=_units))


def cost_of(endpoint: str) -> EndpointCost:
    return ENDPOINT_COSTS.get(endpoint)
