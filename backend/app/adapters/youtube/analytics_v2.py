from pydantic import SecretStr

from app.adapters.youtube.http import ApiRequest, RequestHandler
from app.domain.youtube import QuotaPriority
from app.ports.youtube import AnalyticsQuery, AnalyticsTable, RequestContext


class AnalyticsApiGateway:
    """youtube analytics api v2 reports.query for the owner's own channel (channel==MINE) only."""

    def __init__(self, handler: RequestHandler, *, base_url: str) -> None:
        self._handler = handler
        self._base_url = base_url.rstrip("/")

    async def report(
        self, access_token: SecretStr, query: AnalyticsQuery, ctx: RequestContext | None = None
    ) -> AnalyticsTable:
        params = {
            "ids": "channel==MINE",
            "startDate": query.start_date.isoformat(),
            "endDate": query.end_date.isoformat(),
            "metrics": ",".join(query.metrics),
        }
        optional = {
            "dimensions": ",".join(query.dimensions) if query.dimensions else None,
            "filters": query.filters,
            "sort": query.sort,
            "maxResults": str(query.max_results) if query.max_results else None,
        }
        params.update({name: value for name, value in optional.items() if value})
        request = ApiRequest(
            endpoint="analytics.reports.query",
            url=f"{self._base_url}/reports",
            params=params,
            ctx=ctx or RequestContext(client_id=None, run_id=None, priority=QuotaPriority.daily_refresh),
            bearer=access_token,
        )
        response = await self._handler.send(request)
        headers = [str(header.get("name")) for header in response.body.get("columnHeaders") or []]
        # the api omits "rows" entirely when there is no data for the period
        return AnalyticsTable(column_headers=headers, rows=response.body.get("rows") or [])
