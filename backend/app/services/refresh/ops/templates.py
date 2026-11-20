from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from app.core.registry import Registry
from app.domain.refresh import ClaimLevel, InsertionKind
from app.services.refresh.context import RefreshContext
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.links.urls import add_utm
from app.services.refresh.policy.claims import asserted_level
from app.services.refresh.text import KeywordPresence, content_tokens
from app.services.refresh.triggers.base import Finding, TriggerKind

DEFAULT_LABELS = {
    "book_appointment": "Book an appointment",
    "signup": "Sign up",
    "free_trial": "Start a free trial",
    "demo": "Book a demo",
    "contact_form": "Contact us",
    "quote_request": "Get a quote",
    "call": "Call us",
    "whatsapp": "Message us on WhatsApp",
}
MAX_LABEL_CHARS = 80
LAUNCH_KINDS = frozenset({"model", "product"})


@dataclass(frozen=True, slots=True)
class TemplateLine:
    text: str
    kind: InsertionKind = InsertionKind.insert
    old_text: str | None = None
    keywords_added: tuple[str, ...] = ()
    hashtags_added: tuple[str, ...] = ()
    allowed_urls: frozenset[str] = frozenset()
    rephrasable: bool = False
    vocabulary: frozenset[str] = field(default_factory=frozenset)


def template_line(text: str, **kwargs) -> TemplateLine:
    return TemplateLine(text=text, vocabulary=frozenset(content_tokens(text)), **kwargs)


class InsertionTemplate(Protocol):
    kind: ClassVar[TriggerKind]

    def render(
        self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> TemplateLine | None: ...


template_registry: Registry[InsertionTemplate] = Registry("refresh_template_registry")


def register_template[T: type](cls: T) -> T:
    template_registry.add(cls.kind.value, cls())
    return cls


def _sentence_start(text: str) -> str:
    return text[:1].upper() + text[1:]


def safe_label(label: str, action_type: str, description: str, ctx: RefreshContext) -> str:
    # crawled labels are reused only when they make no claims; otherwise a neutral default is used
    default = DEFAULT_LABELS.get(action_type, "Learn more")
    cleaned = " ".join(label.split()).rstrip(":")
    if not cleaned or len(cleaned) > MAX_LABEL_CHARS or "<" in cleaned or ">" in cleaned:
        return default
    if ctx.policy.violations(cleaned, description) or asserted_level(cleaned) is ClaimLevel.l3_applicability:
        return default
    return cleaned


@register_template
class NewsEntityTemplate:
    kind: ClassVar[TriggerKind] = TriggerKind.news_entity

    def render(self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext) -> TemplateLine:
        params = finding.params
        entity, month = params["entity"], params["month_year"]
        launch = params["entity_kind"] in LAUNCH_KINDS
        if finding.claim_level is ClaimLevel.l3_applicability:
            news = f"{entity} is now available" if launch else entity
            text = f"Update ({month}): {news} — the {params['covered_noun']} shown here applies to it."
        elif launch:
            text = f"Update ({month}): {entity} is now available. This video was recorded before its release."
        else:
            text = f"Update ({month}): {entity}. This video was recorded before this news."
        return template_line(text, keywords_added=(entity,), rephrasable=True)


@register_template
class StaleYearTemplate:
    kind: ClassVar[TriggerKind] = TriggerKind.stale_year

    def render(self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext) -> TemplateLine:
        url = f"https://youtu.be/{finding.params['newer_video_id']}"
        year = finding.params["current_year"]
        text = f"Looking for the latest? Watch our {year} update: {url}"
        return template_line(text, keywords_added=(year,), allowed_urls=frozenset({url}))


@register_template
class BuyerQuestionGapTemplate:
    kind: ClassVar[TriggerKind] = TriggerKind.buyer_question_gap

    def render(self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext) -> TemplateLine:
        phrase = finding.params["phrase"].strip().rstrip("?.!")
        head = finding.params["title_head"]
        text = f"{head}: {phrase}." if head and not KeywordPresence(head).contains(phrase) else f"{phrase}."
        return template_line(
            _sentence_start(text), keywords_added=(finding.params["phrase"],), rephrasable=True
        )


@register_template
class AboveFoldKeywordTemplate:
    kind: ClassVar[TriggerKind] = TriggerKind.above_fold_keyword

    def render(self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext) -> TemplateLine:
        phrase = finding.params["phrase"].strip().rstrip("?.!")
        head = finding.params["title_head"]
        text = f"{head}." if KeywordPresence(head).contains(phrase) else f"{phrase} — {head}."
        return template_line(
            _sentence_start(text), keywords_added=(finding.params["phrase"],), rephrasable=True
        )


class _LinkTemplate:
    def render(self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext) -> TemplateLine:
        params = finding.params
        target = params["url"].removeprefix("tel:")
        if ctx.options.utm_tagging and target.startswith("http"):
            target = add_utm(target, finding.video_id)
        if evidence.language not in ctx.settings.supported_languages:
            return template_line(target, allowed_urls=frozenset({target}))
        label = safe_label(params["label"], params["action_type"], evidence.structure.text, ctx)
        return template_line(f"{label}: {target}", allowed_urls=frozenset({target}))


@register_template
class MissingCtaTemplate(_LinkTemplate):
    kind: ClassVar[TriggerKind] = TriggerKind.missing_cta


@register_template
class MissingContactTemplate(_LinkTemplate):
    kind: ClassVar[TriggerKind] = TriggerKind.missing_contact


@register_template
class MissingHashtagsTemplate:
    kind: ClassVar[TriggerKind] = TriggerKind.missing_hashtags

    def render(self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext) -> TemplateLine:
        tags = tuple(finding.params["hashtags"].split())
        return template_line(" ".join(tags), hashtags_added=tags)


@register_template
class BrokenLinkTemplate:
    kind: ClassVar[TriggerKind] = TriggerKind.broken_link

    def render(self, finding: Finding, evidence: VideoContentEvidence, ctx: RefreshContext) -> TemplateLine:
        repaired = finding.params["new_text"]
        return TemplateLine(
            text=repaired,
            kind=InsertionKind.link_fix,
            old_text=finding.params["old_text"],
            allowed_urls=frozenset({repaired}),
        )
