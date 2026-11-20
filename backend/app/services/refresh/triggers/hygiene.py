from typing import ClassVar

import phonenumbers
import regex

from app.domain.profile import ConversionAction
from app.domain.refresh import Advisory, ClaimLevel, TriggerEvidence
from app.domain.topics import TopicKind
from app.services.refresh.context import RefreshContext
from app.services.refresh.eligibility import VideoEligibility, VideoState
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.links.urls import same_target
from app.services.refresh.text import TOKEN, KeywordPresence, content_tokens
from app.services.refresh.triggers.base import Finding, TriggerKind, dedupe_key, register_detector

WHATSAPP_OR_TEL = regex.compile(r"(wa\.me/|api\.whatsapp\.com|whatsapp\.com/send|\btel:)", regex.IGNORECASE)
MAX_HASHTAG_CHARS = 30


def _evidence(defect: str) -> TriggerEvidence:
    return TriggerEvidence(defect=defect)


def _location_terms(ctx: RefreshContext, location_ids: list[str]) -> list[str]:
    items = [item for item in ctx.profile.locations.items if item.id in location_ids]
    cities = [item.city for item in ctx.profile.locations.items if item.city]
    terms = []
    for item in items:
        terms += [item.name, *item.aliases]
        if item.city and cities.count(item.city) == 1:
            terms.append(item.city)
    return terms


def choose_cta(
    actions: list[ConversionAction], state: VideoState, evidence: VideoContentEvidence, ctx: RefreshContext
) -> tuple[ConversionAction, bool]:
    video_offerings = {
        offering
        for ref_id in evidence.covered_topic_refs
        if (ref := ctx.topic_refs.get(ref_id)) is not None
        for offering in ref.offering_ids
    }
    text = KeywordPresence(f"{state.video.title}\n{state.video.description}")

    def score(action: ConversionAction) -> tuple[float, bool, int]:
        value = 0.0
        if set(action.offering_ids) & video_offerings:
            value += 0.5
        if action.location_ids and text.contains_any(_location_terms(ctx, action.location_ids)):
            value += 0.3
        if action.type == "book_appointment":
            value += 0.2
        generic = action.is_primary and not action.offering_ids and not action.location_ids
        return value, generic, -len(action.offering_ids) - len(action.location_ids)

    best = max(actions, key=score)
    return best, score(best)[0] > 0.2


@register_detector
class MissingCtaDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.missing_cta

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        fits = evidence.fit_score >= ctx.settings.fit_threshold
        if not gate.hygiene_eligible or not state.earning or not fits:
            return []
        actions = ctx.cta_actions()
        if not actions:
            return []
        urls = evidence.structure.urls
        if any(same_target(url.raw, action.target) for url in urls for action in actions):
            return []
        if not gate.cta_eligible:
            if not state.maybe_short:
                return []
            advisory = Advisory(
                kind="shorts_links_not_clickable",
                message=(
                    "Links in Shorts descriptions aren't clickable. Use the 'Related video' option in Studio "
                    "to point viewers to a long video."
                ),
                risk="low",
            )
            return [self._finding(state, actions[0], 0.7, None, (advisory,))]
        action, matched = choose_cta(actions, state, evidence, ctx)
        return [self._finding(state, action, 1.0 if matched else 0.7, ClaimLevel.l0_navigational, ())]

    def _finding(
        self,
        state: VideoState,
        action: ConversionAction,
        strength: float,
        level: ClaimLevel | None,
        advisories: tuple[Advisory, ...],
    ) -> Finding:
        noun = "sign-up" if action.type in ("signup", "free_trial") else "booking"
        return Finding(
            kind=self.kind,
            video_id=state.video_id,
            dedupe_key=dedupe_key(self.kind, action.id),
            strength=strength,
            demand=0.0,
            urgency=0.8,
            evidence=_evidence(f"no {noun} link in the description"),
            claim_level=level,
            params={"url": action.target, "label": action.label, "action_type": action.type},
            advisories=advisories,
        )


@register_detector
class MissingContactDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.missing_contact

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        if not gate.cta_eligible:
            return []
        if not (ctx.decision.review_required or ctx.is_local_business):
            return []
        actions = ctx.contact_actions()
        if not actions:
            return []
        description = state.video.description
        if WHATSAPP_OR_TEL.search(description) or self._has_phone(description, ctx.profile.identity.country):
            return []
        action = choose_cta(actions, state, evidence, ctx)[0]
        return [
            Finding(
                kind=self.kind,
                video_id=state.video_id,
                dedupe_key=dedupe_key(self.kind, action.id),
                strength=0.6,
                demand=0.0,
                urgency=0.5,
                evidence=_evidence("no phone or WhatsApp contact in the description"),
                claim_level=ClaimLevel.l0_navigational,
                params={"url": action.target, "label": action.label, "action_type": action.type},
            )
        ]

    @staticmethod
    def _has_phone(text: str, country: str | None) -> bool:
        return any(True for _ in phonenumbers.PhoneNumberMatcher(text, country))


@register_detector
class MissingChaptersDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.missing_chapters

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        duration = state.video.duration_s or 0
        if not gate.hygiene_eligible or not state.earning or state.maybe_short:
            return []
        if duration < ctx.settings.chapters_min_duration_s or evidence.structure.chapters_valid:
            return []
        structure = evidence.structure
        if structure.chapters:
            problems = " and ".join(structure.chapter_problems)
            advisory = Advisory(
                kind="invalid_chapters",
                message=f"Your timestamps won't show as chapters: {problems}.",
                risk="low",
            )
            defect = "timestamps don't form valid chapters"
        else:
            # chapter text is never invented without the owner's transcript (99 G10)
            follow_up = (
                "Add timestamps starting at 00:00 in Studio."
                if ctx.oauth_connected
                else "We can draft them if you connect your channel."
            )
            advisory = Advisory(
                kind="chapters_needed",
                message=f"Adding chapters helps viewers jump to the answer. {follow_up}",
                risk="low",
            )
            defect = "no chapters"
        return [
            Finding(
                kind=self.kind,
                video_id=state.video_id,
                dedupe_key=dedupe_key(self.kind, "chapters"),
                strength=0.6,
                demand=0.0,
                urgency=0.4,
                evidence=_evidence(defect),
                advisories=(advisory,),
            )
        ]


def hashtag_for(phrase: str) -> str | None:
    # keeps acronyms as written ("AI tools" -> #AITools) and capitalises the rest
    words = TOKEN.findall(phrase)
    tag = "#" + "".join(word if word.isupper() else word[:1].upper() + word[1:] for word in words)
    if not words or len(tag) > MAX_HASHTAG_CHARS or not tag[1:].isalnum():
        return None
    return tag


@register_detector
class MissingHashtagsDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.missing_hashtags

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        settings = ctx.settings
        if not gate.hygiene_eligible or not state.earning:
            return []
        existing = len(evidence.structure.hashtags) + state.video.title.count("#")
        if existing > settings.hashtags_ignored_over:
            advisory = Advisory(
                kind="too_many_hashtags",
                message=f"YouTube ignores all hashtags on videos with more than {settings.hashtags_ignored_over}.",
                risk="medium",
            )
            return [
                Finding(
                    kind=self.kind,
                    video_id=state.video_id,
                    dedupe_key=dedupe_key(self.kind, "too many"),
                    strength=0.5,
                    demand=0.0,
                    urgency=0.3,
                    evidence=_evidence(f"{existing} hashtags"),
                    advisories=(advisory,),
                )
            ]
        if existing:
            return []
        tags = self._candidate_tags(evidence, ctx)[: settings.max_new_hashtags]
        if not tags:
            return []
        return [
            Finding(
                kind=self.kind,
                video_id=state.video_id,
                dedupe_key=dedupe_key(self.kind, "hashtags"),
                strength=0.5,
                demand=0.0,
                urgency=0.3,
                evidence=_evidence("no hashtags"),
                claim_level=ClaimLevel.l2_coverage,
                params={"hashtags": " ".join(tags)},
            )
        ]

    @staticmethod
    def _candidate_tags(evidence: VideoContentEvidence, ctx: RefreshContext) -> list[str]:
        ranked: list[tuple[float, str]] = []
        for ref_id, score in evidence.topic_scores.items():
            ref = ctx.topic_refs.get(ref_id)
            if ref is not None and ref.kind in (TopicKind.keyword, TopicKind.offering):
                ranked.append((score, ref.label))
        ranked += [(keyword.weight, keyword.term) for keyword in ctx.profile.niche_keywords]
        tags: list[str] = []
        for _, phrase in sorted(ranked, key=lambda item: item[0], reverse=True):
            terms = content_tokens(phrase)
            if not terms or not all(term in evidence.covered_tokens for term in terms):
                continue
            tag = hashtag_for(phrase)
            if tag and tag.casefold() not in {existing.casefold() for existing in tags}:
                tags.append(tag)
        return tags


@register_detector
class MissingDisclaimerDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.missing_disclaimer

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        disclaimer = ctx.channel_disclaimer
        if not gate.hygiene_eligible or not ctx.policy.wants_disclaimer() or disclaimer is None:
            return []
        if evidence.structure.disclaimer_line is not None:
            return []
        return [
            Finding(
                kind=self.kind,
                video_id=state.video_id,
                dedupe_key=dedupe_key(self.kind, "disclaimer"),
                strength=0.8,
                demand=0.0,
                urgency=0.6,
                evidence=_evidence("standard disclaimer missing"),
                advisories=(
                    Advisory(
                        kind="manual_review",
                        message=(
                            "Most of your videos include your standard disclaimer; this one doesn't: "
                            f"“{disclaimer.strip()}”"
                        ),
                        risk="medium",
                    ),
                ),
            )
        ]
