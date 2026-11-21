from app.api.services import TRENDS, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.trends import TrendScan
from app.pipeline.inputs.common import latest_artifact
from app.pipeline.stages.trends import TREND_SCAN

NOT_SCANNED = "Trends appear after your first analysis finishes."


class TrendReadService(ServiceBase):
    async def latest(self, scope: TenantScope, window_days: int) -> TrendScan:
        # cards and news-only runs publish without a trend scan, so read the newest scan across runs
        scan = await latest_artifact(self.container, scope, TREND_SCAN)
        if scan is None:
            return TrendScan(as_of=self.clock.now(), window_days=window_days, messages=[NOT_SCANNED])
        return scan


@api_service_registry.register(TRENDS)
def build_trend_service(container: Container) -> TrendReadService:
    return TrendReadService(container)
