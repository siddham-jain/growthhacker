from typing import Any, Literal

from app.domain.common import DomainModel
from app.domain.web import SourceKind, UserAgentMode
from app.domain.youtube import ChannelRecord
from app.ports.context import AcquisitionRequest, AcquisitionResult, BlockedAttempt
from app.services.website_intel.crawl.robots import RobotsRules, robots_from_response
from app.services.website_intel.identity.knowledge import KbEntity
from app.services.website_intel.models import ExtractedPage, InventoryUrl, NavItem, TemplateSummary


class RobotsSnapshot(DomainModel):
    status_code: int | None
    body: str | None
    token: str

    def rules(self) -> RobotsRules:
        return robots_from_response(self.status_code, self.body, token=self.token)


class UserMaterial(DomainModel):
    kind: Literal["text", "markdown", "pdf_text", "url_list"]
    content: str
    title: str | None = None
    source_url: str | None = None


class CascadeRequest(AcquisitionRequest):
    # what later tiers need from earlier ones; the shared request has no slot for it
    candidate_urls: list[str] = []
    fetched_urls: list[str] = []
    prior_blocked: list[BlockedAttempt] = []
    robots: RobotsSnapshot | None = None
    channel: ChannelRecord | None = None
    upload_texts: list[str] = []
    knowledge_entity: KbEntity | None = None
    user_material: list[UserMaterial] = []
    home_covered: bool = False

    def robots_allows(self, url: str) -> bool:
        # third-party copies skip only urls our own robots check explicitly disallows (plan 03 §14)
        if self.robots is None:
            return True
        return not self.robots.rules().explicitly_disallows(url)


class SourceHarvest(AcquisitionResult):
    extracted: list[ExtractedPage] = []
    inventory: list[InventoryUrl] = []
    templates: list[TemplateSummary] = []
    nav: list[NavItem] = []
    remaining_urls: list[str] = []
    robots: RobotsSnapshot | None = None
    ua_mode: UserAgentMode | None = None
    knowledge_entity: KbEntity | None = None
    notes: list[str] = []
    stats: dict[str, int] = {}


def harvest(source_kind: SourceKind, extracted: list[ExtractedPage], **fields: Any) -> SourceHarvest:
    return SourceHarvest(
        source_kind=source_kind, pages=[page.page for page in extracted], extracted=extracted, **fields
    )
