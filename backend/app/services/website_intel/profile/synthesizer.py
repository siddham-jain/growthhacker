import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from typing import Any, get_args

from rapidfuzz import fuzz

from app.domain.profile import (
    BrandTerm,
    BusinessIdentity,
    BusinessModel,
    ChannelLink,
    ClaimOrigin,
    CompetitorHint,
    ConversionAction,
    Evidence,
    Fact,
    FactKind,
    Location,
    LocationSummary,
    Offering,
    PriceSignal,
    Prominence,
    RelatedBrand,
    StatedAudience,
    ValueProposition,
)
from app.domain.web import SiteRef
from app.ports.llm import PayloadClass, PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.services.website_intel.extract.injection import contains_instruction
from app.services.website_intel.identity.locator import LocatedBusinessDetail
from app.services.website_intel.identity.related_brands import BrandSignal
from app.services.website_intel.inputs.urls import InvalidUrlError, normalize_url, path_of
from app.services.website_intel.models import ExtractedPage, NavItem, TemplateSummary
from app.services.website_intel.profile.claims import ClaimFactory, normalise_name, stable_id
from app.services.website_intel.profile.deterministic import DeterministicTables
from app.services.website_intel.profile.evidence import FactStore, numbers_in
from app.services.website_intel.profile.industry import industry_from_text, normalise_industry
from app.services.website_intel.profile.llm import LlmGateway
from app.services.website_intel.profile.prompts import PROFILE_CLAIMS_PROMPT
from app.services.website_intel.profile.schemas import (
    AudienceKind,
    BusinessModelValue,
    ClaimsOutput,
    CoreProfileOutput,
    GeoScope,
    HintType,
    LlmBrandTerm,
    LlmConversion,
    LlmLocation,
    LlmOffering,
    LocationKind,
    OfferingKind,
    ValuePropKind,
)

CAPS = {
    "offerings": 120,
    "value_props": 40,
    "audiences": 30,
    "locations": 200,
    "ctas": 20,
    "brands": 40,
    "hints": 20,
}
CHARS_PER_TOKEN = 4
NAME_MATCH_MIN = 80
DEMOGRAPHIC_TERMS = re.compile(
    r"\b(?:aged?\s*\d|\d{2}\s*[-–]\s*\d{2}\s*(?:years|yrs)?|years? old|men|women|male|female|gender|income|"
    r"affluent|wealthy|low-income|religio\w*|hindu|muslim|christian|caste|ethnic\w*|married|single|divorced|"
    r"graduates?|millennials?|gen ?z|boomers?)\b",
    re.IGNORECASE,
)
CTA_MEDIUM_PRIMARY = ("book_appointment", "demo", "signup", "free_trial", "buy", "quote_request")
FACT_KIND_ORDER = (
    FactKind.offering,
    FactKind.price_binding,
    FactKind.value_prop,
    FactKind.credential,
    FactKind.statistic,
    FactKind.audience_statement,
    FactKind.location,
    FactKind.conversion,
    FactKind.faq,
    FactKind.brand_term,
    FactKind.entity,
    FactKind.competitor_mention,
    FactKind.freshness_cue,
    FactKind.keyword_phrase,
)


CORE_KINDS = (
    FactKind.offering,
    FactKind.price_binding,
    FactKind.brand_term,
    FactKind.conversion,
    FactKind.value_prop,
    FactKind.entity,
    FactKind.faq,
    FactKind.keyword_phrase,
)
CLAIM_KINDS = (
    FactKind.value_prop,
    FactKind.credential,
    FactKind.statistic,
    FactKind.audience_statement,
    FactKind.location,
    FactKind.competitor_mention,
)


def _country_timezones() -> dict[str, str]:
    table = resources.files("tzdata").joinpath("zoneinfo/zone1970.tab").read_text(encoding="utf-8")
    zones: dict[str, str] = {}
    for line in table.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        codes, _, zone = line.split("\t")[:3]
        for code in codes.split(","):
            zones.setdefault(code, zone)
    return zones


COUNTRY_TIMEZONES = _country_timezones()


@dataclass
class SynthesisContext:
    store: FactStore
    tables: DeterministicTables
    pages: Sequence[ExtractedPage]
    nav: Sequence[NavItem]
    templates: Sequence[TemplateSummary]
    inventory_urls: frozenset[str]
    site: SiteRef | None
    located: LocatedBusinessDetail | None
    claims: ClaimFactory
    brand_signals: Sequence[BrandSignal] = ()
    country_hint: str | None = None
    coverage_notes: Sequence[str] = ()


@dataclass
class CoreBundle:
    identity: BusinessIdentity
    offerings: list[Offering]
    value_propositions: list[ValueProposition]
    target_customers: list[StatedAudience]
    locations: LocationSummary
    conversion_actions: list[ConversionAction]
    brand_terms: list[BrandTerm]
    competitor_hints: list[CompetitorHint]
    used_llm: bool
    warnings: list[str] = field(default_factory=list)
    dropped: int = 0


def choice(value: str, options: Any, default: str) -> Any:
    cleaned = re.sub(r"[\s-]+", "_", value.strip().lower())
    return cleaned if cleaned in get_args(options) else default


def fact_lines(
    store: FactStore, token_cap: int, kinds: Sequence[FactKind] = FACT_KIND_ORDER
) -> tuple[str, bool]:
    lines: list[str] = []
    budget = token_cap * CHARS_PER_TOKEN
    size = 0
    truncated = False
    for kind in kinds:
        group = store.of_kind(kind)
        if not group:
            continue
        header = f"## {kind.value}"
        lines.append(header)
        size += len(header)
        for fact in group:
            line = (
                f"{fact.fact_id} | {fact.kind.value} | {fact.text} | {path_of(fact.source_url)} | "
                f"{fact.source_kind.value}{'' if not fact.attrs else ' | ' + ', '.join(f'{k}={v}' for k, v in fact.attrs.items() if k not in {'answer_quote'})[:200]}"
            )
            if size + len(line) > budget:
                truncated = True
                break
            lines.append(line)
            size += len(line)
    return "\n".join(lines), truncated


@dataclass(frozen=True)
class Draft:
    core: CoreProfileOutput
    claims: ClaimsOutput


class ProfileSynthesizer:
    """r1 as two partitioned reduce calls that cite fact ids only, bound deterministically (plan 03 §5.10)."""

    def __init__(self, llm: LlmGateway, *, token_cap: int) -> None:
        self._llm = llm
        self._token_cap = token_cap

    async def synthesize(self, ctx: SynthesisContext) -> CoreBundle:
        fallback = DeterministicDraft(ctx).build()
        core, claims, warnings = await self._llm_draft(ctx)
        draft = Draft(core=core or fallback.core, claims=claims or fallback.claims)
        bundle = CoreBinder(ctx).bind(draft, used_llm=core is not None and claims is not None)
        bundle.warnings.extend(warnings)
        return bundle

    def _context(self, ctx: SynthesisContext) -> TrustedBlock:
        return TrustedBlock(
            label="context",
            text=(
                f"Website: {ctx.site.canonical_origin if ctx.site else 'none'}\n"
                f"Name guess: {ctx.located.business_name if ctx.located and ctx.located.business_name else 'unknown'}\n"
                f"Country hint: {ctx.country_hint or 'unknown'}\n"
                f"Coverage notes: {'; '.join(ctx.coverage_notes) or 'none'}"
            ),
        )

    async def _llm_draft(
        self, ctx: SynthesisContext
    ) -> tuple[CoreProfileOutput | None, ClaimsOutput | None, list[str]]:
        if not self._llm.available or not ctx.store.facts:
            return None, None, []
        core_facts, core_cut = fact_lines(ctx.store, self._token_cap // 2, CORE_KINDS)
        claim_facts, claim_cut = fact_lines(ctx.store, self._token_cap // 2, CLAIM_KINDS)
        warnings = ["reduce input truncated to the token cap"] if core_cut or claim_cut else []
        ctas = "\n".join(
            f"{row.cta_id}: {row.candidate.type} | {row.candidate.label[:60]} | {row.candidate.target[:120]} | "
            f"pages={len(row.pages)} home={row.on_home} header={row.in_header}"
            for row in ctx.tables.ctas.values()
        )
        prices = "\n".join(
            f"{row.price_id}: {row.candidate.raw_text} | {row.candidate.heading or ''} | {path_of(row.candidate.page_url)}"
            for row in list(ctx.tables.prices.values())[:200]
        )
        nav = "\n".join(
            f"{'  ' * (item.depth - 1)}{item.label} -> {path_of(item.url)}" for item in ctx.nav[:200]
        )
        templates = "\n".join(f"{item.template_key} x{item.count}" for item in ctx.templates[:40])
        payload = frozenset({PayloadClass.public_web, PayloadClass.public_youtube})
        core = await self._llm.structured(
            TaskId.website_profile_reduce,
            CoreProfileOutput,
            PromptInput(
                trusted=[self._context(ctx)],
                untrusted=[
                    UntrustedBlock(label="facts", source="verified_facts", text=core_facts or "none"),
                    UntrustedBlock(label="calls_to_action", source="cta_table", text=ctas or "none"),
                    UntrustedBlock(label="prices", source="price_table", text=prices or "none"),
                    UntrustedBlock(label="navigation", source="site_nav", text=nav or "none"),
                    UntrustedBlock(label="templates", source="url_inventory", text=templates or "none"),
                ],
            ),
            payload=payload,
            max_output_tokens=12_000,
        )
        offering_names = "\n".join(item.name for item in (core.offerings if core else [])[:120])
        claims = await self._llm.structured(
            TaskId.website_profile_reduce,
            ClaimsOutput,
            PromptInput(
                trusted=[self._context(ctx)],
                untrusted=[
                    UntrustedBlock(label="facts", source="verified_facts", text=claim_facts or "none"),
                    UntrustedBlock(label="offerings", source="reduce_output", text=offering_names or "none"),
                    UntrustedBlock(
                        label="location_candidates",
                        source="url_inventory",
                        text=", ".join(ctx.tables.location_values[:60]) or "none",
                    ),
                ],
            ),
            payload=payload,
            max_output_tokens=8_000,
            prompt_id=PROFILE_CLAIMS_PROMPT,
        )
        return core, claims, warnings


def _offering(fact: Fact) -> LlmOffering:
    return LlmOffering(
        name=fact.text,
        kind="service",
        description="",
        url=fact.attrs.get("url", ""),
        parent_name="",
        is_primary=False,
        price_ids=[],
        evidence_ids=[fact.fact_id],
    )


def _location(fact: Fact) -> LlmLocation:
    return LlmLocation(
        name=fact.text,
        kind="branch",
        address=fact.attrs.get("address", ""),
        city=fact.attrs.get("city", ""),
        region=fact.attrs.get("region", ""),
        country=fact.attrs.get("country", ""),
        evidence_ids=[fact.fact_id],
    )


class DeterministicDraft:
    """the llm-free core profile: json-ld, meta, nav, kb, channel and cta tables only."""

    def __init__(self, ctx: SynthesisContext) -> None:
        self._ctx = ctx

    def _role(self, *roles: str) -> list[Fact]:
        return [
            fact for fact in self._ctx.store.of_kind(FactKind.brand_term) if fact.attrs.get("role") in roles
        ]

    def name_facts(self) -> list[Fact]:
        return (
            self._role("name", "legalName")
            or self._role("site_name")
            or self._role("kb_label")
            or self._role("title")
            or self._role("channel_title")
        )

    def locations(self) -> list[LlmLocation]:
        return [_location(fact) for fact in self._ctx.store.of_kind(FactKind.location)]

    def build(self) -> Draft:
        store = self._ctx.store
        name_facts = self.name_facts()
        guess = self._ctx.located.business_name if self._ctx.located else None
        fallback_name = guess or (self._ctx.site.registrable_domain if self._ctx.site else "Unknown")
        name = name_facts[0].text if name_facts else fallback_name
        descriptions = [
            fact
            for fact in store.of_kind(FactKind.value_prop, FactKind.entity)
            if fact.attrs.get("role") == "meta_description"
        ]
        kb_entities = [
            fact for fact in store.of_kind(FactKind.entity) if fact.attrs.get("kb_field") == "Industry"
        ]
        basis = [fact.fact_id for fact in [*descriptions[:1], *kb_entities[:2], *name_facts[:1]]]
        industry_text = " ".join(fact.text for fact in [*kb_entities, *descriptions[:2]])
        locations = self.locations()
        count = len(locations)
        brands = [
            LlmBrandTerm(term=fact.text, kind="company", variants=[], evidence_ids=[fact.fact_id])
            for fact in self._role("name", "legalName", "alternateName", "site_name", "kb_label")
        ]
        geo_scope = "national" if count >= 10 or count == 0 else "regional" if count >= 2 else "local"
        core = CoreProfileOutput(
            name=name,
            name_evidence_ids=[fact.fact_id for fact in name_facts[:3]],
            legal_name="",
            one_liner=(descriptions[0].text if descriptions else name)[:160],
            one_liner_basis_ids=basis,
            description="",
            industry=industry_from_text(industry_text),
            industry_basis_ids=basis,
            niche="",
            business_model="multi_location_service" if count > 1 else "other",
            geo_scope=geo_scope,
            primary_language="en",
            languages=[],
            country="",
            offerings=[_offering(fact) for fact in store.of_kind(FactKind.offering)],
            conversions=[
                LlmConversion(cta_id=row.cta_id, offering_names=[], is_primary=False)
                for row in self._ctx.tables.ctas.values()
            ],
            brand_terms=brands,
        )
        claims = ClaimsOutput(value_propositions=[], audiences=[], locations=locations, competitor_hints=[])
        return Draft(core=core, claims=claims)


class CoreBinder:
    """resolves fact ids to evidence and drops anything unsupported (plan 03 §5.11.1–7)."""

    def __init__(self, ctx: SynthesisContext) -> None:
        self._ctx = ctx
        self._store = ctx.store
        self._claims = ctx.claims
        self.dropped = 0
        self._allowed_urls = (
            {page.page.url for page in ctx.pages}
            | {page.page.canonical_url for page in ctx.pages}
            | set(ctx.inventory_urls)
            | {item.url for item in ctx.nav}
        )

    def _evidence(self, ids: Iterable[str]) -> list[Evidence]:
        return self._store.evidence_for(fact_id for fact_id in ids if fact_id in self._store.facts)

    def _basis(self, ids: Iterable[str]) -> list[str]:
        return [fact_id for fact_id in ids if fact_id in self._store.facts]

    def _url(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            url = normalize_url(value, base=self._ctx.site.canonical_origin if self._ctx.site else None)
        except InvalidUrlError:
            return None
        return (
            url
            if url in self._allowed_urls or url.rstrip("/") in {u.rstrip("/") for u in self._allowed_urls}
            else None
        )

    def bind(self, pair: Draft, *, used_llm: bool) -> CoreBundle:
        draft, claims = pair.core, pair.claims
        warnings: list[str] = []
        offerings = self._offerings(draft.offerings)
        by_name = {normalise_name(offering.name): offering.id for offering in offerings}
        identity = self._identity(draft, warnings)
        bundle = CoreBundle(
            identity=identity,
            offerings=offerings,
            value_propositions=self._value_props(claims, by_name),
            target_customers=self._audiences(claims),
            locations=self._locations([*claims.locations, *DeterministicDraft(self._ctx).locations()]),
            conversion_actions=self._conversions(draft.conversions, by_name),
            brand_terms=self._brands(draft.brand_terms),
            competitor_hints=[],
            used_llm=used_llm,
            warnings=warnings,
        )
        bundle.competitor_hints = self._hints(claims, bundle)
        bundle.dropped = self.dropped
        return bundle

    def _identity(self, draft: CoreProfileOutput, warnings: list[str]) -> BusinessIdentity:
        ctx = self._ctx
        name_evidence = self._evidence(draft.name_evidence_ids)
        if not name_evidence:
            fallback_facts = DeterministicDraft(ctx).name_facts()
            name_evidence = self._evidence(fact.fact_id for fact in fallback_facts[:3])
            if name_evidence:
                draft = draft.model_copy(update={"name": fallback_facts[0].text})
        references = [
            fact.text for fact in self._store.of_kind(FactKind.brand_term) if fact.attrs.get("role")
        ]
        if ctx.located and ctx.located.channel_title:
            references.append(ctx.located.channel_title)
        if references and max(fuzz.token_set_ratio(draft.name, ref) for ref in references) < NAME_MATCH_MIN:
            warnings.append("business name needs confirmation")
        name_meta = (
            self._claims.evidenced(name_evidence, origin=ClaimOrigin.deterministic)
            if name_evidence
            else self._claims.based(["identity"], origin=ClaimOrigin.inferred)
        )
        basis = self._basis(draft.one_liner_basis_ids) or self._basis(draft.name_evidence_ids) or ["identity"]
        industry_basis = self._basis(draft.industry_basis_ids) or basis
        languages = Counter(page.page.lang.split("-")[0] for page in ctx.pages if page.page.lang)
        primary_language = (
            (languages.most_common(1)[0][0] if languages else None) or draft.primary_language or "en"
        )
        country = (draft.country or ctx.country_hint or "").upper()[:2] or None
        if country is not None and not re.fullmatch(r"[A-Z]{2}", country):
            country = None
        site = ctx.site
        located = ctx.located
        channel = None
        if located and located.channel_id and located.ownership is not None:
            channel = ChannelLink(
                channel_id=located.channel_id,
                handle=located.channel_handle,
                ownership=located.ownership,
                resolved_via=located.channel_resolved_via or "user",
            )
        related = [
            RelatedBrand(
                name=signal.name[:120],
                domain=signal.domain,
                relation=signal.relation,
                meta=self._claims.evidenced([signal.evidence], origin=ClaimOrigin.deterministic),
            )
            for signal in ctx.brand_signals
        ]
        one_liner = draft.one_liner if not contains_instruction(draft.one_liner) else draft.name
        return BusinessIdentity(
            name=draft.name[:200],
            name_meta=name_meta,
            legal_name=draft.legal_name or None,
            one_liner=one_liner[:160],
            one_liner_meta=self._claims.based(basis),
            description=(draft.description or None) and draft.description[:600],
            industry=normalise_industry(draft.industry),
            industry_meta=self._claims.based(industry_basis),
            niche=draft.niche[:80],
            business_model=BusinessModel(choice(draft.business_model, BusinessModelValue, "other")),
            geo_scope=choice(draft.geo_scope, GeoScope, "national"),
            primary_language=primary_language,
            languages=[language for language, _ in languages.most_common(10)] or [primary_language],
            country=country,
            timezone=COUNTRY_TIMEZONES.get(country) if country else None,
            website=site,
            website_domains=[site.registrable_domain] if site else [],
            brand_aliases=sorted({term.term for term in draft.brand_terms if term.term != draft.name})[:20],
            primary_markets=[country] if country else [],
            channel=channel,
            related_brands=related,
        )

    def _offerings(self, items: Sequence[LlmOffering]) -> list[Offering]:
        result: dict[str, Offering] = {}
        parents: dict[str, str] = {}
        for item in items[: CAPS["offerings"]]:
            evidence = self._evidence(item.evidence_ids)
            if not evidence or contains_instruction(item.name):
                self.dropped += 1
                continue
            url = self._url(item.url)
            offering_id = stable_id("off", item.name, url)
            if offering_id in result:
                continue
            prices = [
                self._price(price_id) for price_id in item.price_ids if price_id in self._ctx.tables.prices
            ]
            nav_hit = any(
                normalise_name(nav.label) == normalise_name(item.name)
                for nav in self._ctx.nav
                if nav.depth <= 1
            )
            result[offering_id] = Offering(
                id=offering_id,
                name=item.name[:120],
                kind=choice(item.kind, OfferingKind, "other"),
                description=(item.description or None) and item.description[:400],
                url=url,
                price_signals=prices,
                is_primary=item.is_primary or nav_hit,
                priority=1 if item.is_primary or nav_hit else 2,
                meta=self._claims.evidenced(evidence),
            )
            if item.parent_name:
                parents[offering_id] = normalise_name(item.parent_name)
        by_name = {normalise_name(offering.name): offering.id for offering in result.values()}
        for offering_id, parent in parents.items():
            parent_id = by_name.get(parent)
            if parent_id and parent_id != offering_id:
                result[offering_id] = result[offering_id].model_copy(update={"parent_id": parent_id})
        return list(result.values())

    def _price(self, price_id: str) -> PriceSignal:
        row = self._ctx.tables.prices[price_id]
        candidate = row.candidate
        return PriceSignal(
            raw_text=candidate.raw_text,
            amount_min=Decimal(candidate.amount_min) if candidate.amount_min is not None else None,
            amount_max=Decimal(candidate.amount_max) if candidate.amount_max is not None else None,
            currency=candidate.currency,
            period=candidate.period,
            qualifier=candidate.qualifier,
            meta=self._claims.evidenced([row.evidence], origin=ClaimOrigin.deterministic),
        )

    def _value_props(self, draft: ClaimsOutput, by_name: dict[str, str]) -> list[ValueProposition]:
        result = []
        for item in draft.value_propositions[: CAPS["value_props"]]:
            evidence = self._evidence(item.evidence_ids)
            quotes = " ".join(entry.quote for entry in evidence)
            if (
                not evidence
                or not numbers_in(item.statement) <= numbers_in(quotes)
                or contains_instruction(item.statement)
            ):
                self.dropped += 1
                continue
            result.append(
                ValueProposition(
                    id=stable_id("vp", item.statement),
                    statement=item.statement[:200],
                    kind=choice(item.kind, ValuePropKind, "other"),
                    proof_points=[point for point in item.proof_points if point in quotes][:5],
                    offering_ids=[
                        by_name[n] for n in map(normalise_name, item.offering_names) if n in by_name
                    ],
                    meta=self._claims.evidenced(evidence),
                )
            )
        return result

    def _audiences(self, draft: ClaimsOutput) -> list[StatedAudience]:
        result = []
        for item in draft.audiences[: CAPS["audiences"]]:
            evidence = self._evidence(item.evidence_ids)
            quotes = " ".join(entry.quote for entry in evidence).casefold()
            invented = [
                term for term in DEMOGRAPHIC_TERMS.findall(item.segment) if term.casefold() not in quotes
            ]
            if not evidence or invented:
                self.dropped += 1
                continue
            result.append(
                StatedAudience(
                    id=stable_id("aud", item.segment),
                    segment=item.segment[:120],
                    kind=choice(item.kind, AudienceKind, "other"),
                    meta=self._claims.evidenced(evidence),
                )
            )
        return result

    def _locations(self, items: Sequence[LlmLocation]) -> LocationSummary:
        seen: dict[str, Location] = {}
        for item in items:
            evidence = self._evidence(item.evidence_ids)
            if not evidence:
                self.dropped += 1
                continue
            country = item.country.upper()[:2] if re.fullmatch(r"[A-Za-z]{2}", item.country or "") else None
            key = stable_id("loc", country, item.city, item.name)
            if key in seen:
                continue
            seen[key] = Location(
                id=key,
                name=item.name[:160],
                kind=choice(item.kind, LocationKind, "other"),
                address=item.address or None,
                city=item.city or None,
                region=item.region or None,
                country=country,
                meta=self._claims.evidenced(evidence),
            )
        items_list = list(seen.values())
        return LocationSummary(
            count=len(items_list),
            cities=sorted({item.city for item in items_list if item.city}),
            regions=sorted({item.region for item in items_list if item.region}),
            countries=sorted({item.country for item in items_list if item.country}),
            items=items_list[: CAPS["locations"]],
        )

    def _conversions(self, items: Sequence[LlmConversion], by_name: dict[str, str]) -> list[ConversionAction]:
        rows = {row.cta_id: row for row in self._ctx.tables.ctas.values()}
        bindings = {item.cta_id: item for item in items if item.cta_id in rows}
        ranked = sorted(
            rows.values(),
            key=lambda row: (-(len(row.pages) + 3 * row.on_home + 2 * row.in_header), row.cta_id),
        )
        result = []
        for row in ranked[: CAPS["ctas"]]:
            binding = bindings.get(row.cta_id)
            candidate = row.candidate
            result.append(
                ConversionAction(
                    id=stable_id("cta", candidate.type, candidate.target),
                    type=candidate.type,
                    label=(candidate.label or candidate.target)[:120],
                    target=candidate.target,
                    medium=candidate.medium,
                    prominence=Prominence(
                        pages_seen=len(row.pages),
                        on_home=row.on_home,
                        in_header=row.in_header,
                        in_footer=row.in_footer,
                    ),
                    is_primary=bool(binding and binding.is_primary),
                    offering_ids=[
                        by_name[n]
                        for n in map(normalise_name, binding.offering_names if binding else [])
                        if n in by_name
                    ],
                    meta=self._claims.evidenced(row.evidence, origin=ClaimOrigin.deterministic),
                )
            )
        if result and not any(action.is_primary for action in result):
            primary = next((action for action in result if action.type in CTA_MEDIUM_PRIMARY), result[0])
            result = [action.model_copy(update={"is_primary": action.id == primary.id}) for action in result]
        return result

    def _brands(self, items: Sequence[LlmBrandTerm]) -> list[BrandTerm]:
        result: dict[str, BrandTerm] = {}
        for item in items[: CAPS["brands"]]:
            evidence = self._evidence(item.evidence_ids)
            key = normalise_name(item.term)
            if not evidence or not key or key in result:
                continue
            result[key] = BrandTerm(
                term=item.term[:80],
                kind=item.kind,
                variants=item.variants[:10],
                meta=self._claims.evidenced(evidence),
            )
        return list(result.values())

    def _hints(self, draft: ClaimsOutput, bundle: CoreBundle) -> list[CompetitorHint]:
        own = {normalise_name(term.term) for term in bundle.brand_terms} | {
            normalise_name(bundle.identity.name)
        }
        own |= {normalise_name(brand.name) for brand in bundle.identity.related_brands}
        own_domains = set(bundle.identity.website_domains) | {
            brand.domain for brand in bundle.identity.related_brands if brand.domain
        }
        result = []
        for item in draft.competitor_hints[: CAPS["hints"]]:
            if normalise_name(item.name) in own or (item.domain and item.domain in own_domains):
                self.dropped += 1
                continue
            evidence = self._evidence(item.evidence_ids)
            hint_type = choice(item.hint_type, HintType, "category_peer_inferred")
            if hint_type == "category_peer_inferred":
                meta = self._claims.based(
                    self._basis(item.basis_ids) or ["offerings"], origin=ClaimOrigin.inferred
                )
            elif evidence:
                meta = self._claims.evidenced(evidence)
            else:
                self.dropped += 1
                continue
            result.append(
                CompetitorHint(
                    name=item.name[:120], domain=item.domain or None, hint_type=hint_type, meta=meta
                )
            )
        return result
