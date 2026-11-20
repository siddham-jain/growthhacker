import asyncio
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.config import RefreshSettings
from app.domain.confidence import ConfidenceBasis
from app.domain.profile import BusinessProfile
from app.domain.recommendations import GateAction, RecommendationDraft
from app.domain.refresh import (
    DESCRIPTION_BYTE_LIMIT,
    Advisory,
    ClaimLevel,
    DescriptionRefresh,
    RefreshImpact,
    RefreshStatus,
)
from app.domain.topics import TopicKind, TopicMatch
from app.ports.llm import LLMClient
from app.services.refresh import triggers as _detectors  # noqa: F401  registers the detectors
from app.services.refresh.context import CTA_TYPES, RefreshContext
from app.services.refresh.demand import DemandEvidenceCollector
from app.services.refresh.effect import expected_effect
from app.services.refresh.eligibility import (
    BusinessEligibilityEvaluator,
    EligibilityDecision,
    VideoEligibility,
    VideoGate,
    VideoState,
    build_video_states,
)
from app.services.refresh.evidence import (
    VideoContentEvidence,
    boilerplate_lines,
    build_evidence,
    mine_boilerplate,
)
from app.services.refresh.gates import AdditiveDiffOnly, MinimalEdit
from app.services.refresh.impact import ImpactCalculator
from app.services.refresh.inputs import RefreshInputs, StoredRefresh
from app.services.refresh.lifecycle import RefreshLifecycle
from app.services.refresh.links.health import LinkHealthService
from app.services.refresh.links.status import LinkStatus, LinkStatusClass
from app.services.refresh.links.urls import normalise_url, same_target
from app.services.refresh.mapping import to_recommendation_draft
from app.services.refresh.ops.merge import Candidate, MergeResult, RefreshMerger
from app.services.refresh.ops.phrasing import InsertionPhraser, PhrasingResult
from app.services.refresh.ops.planner import OperationPlanner
from app.services.refresh.ops.templates import template_registry
from app.services.refresh.ops.validator import InsertionValidator, is_repeat, requires_variation
from app.services.refresh.output import DroppedSuggestion, HygieneReport, RefreshOutput
from app.services.refresh.policy.domain import policy_for
from app.services.refresh.scoring import (
    RefreshScorer,
    keyword_confidence,
    percentile_ranks,
    rule_based_confidence,
)
from app.services.refresh.structure import DISCLAIMER
from app.services.refresh.text import KeywordPresence, normalise
from app.services.refresh.triggers.base import Finding, trigger_registry
from app.services.refresh.triggers.keywords import topic_phrases

NEXT_WINDOW = {7: 30, 30: 90}
PUBLIC_MODE_FOOTER = "Prepared from public data. Only the channel owner can apply these."
REVIEW_NOTE = "Have your medical/compliance team review before publishing."
UNSUPPORTED_LANGUAGE = "We only suggest wording in English right now. Link fixes below still apply."
NO_ACTIONS = (
    "We couldn't find a booking or sign-up page on your website. Add one in Settings to get link suggestions."
)


@dataclass(slots=True)
class _Scored:
    finding: Finding
    priority: float


@dataclass(slots=True)
class _RunState:
    messages: list[str]
    dropped: list[DroppedSuggestion]

    def note(self, message: str) -> None:
        if message not in self.messages:
            self.messages.append(message)

    def drop(self, finding: Finding, reason: str) -> None:
        self.dropped.append(
            DroppedSuggestion(
                video_id=finding.video_id,
                trigger_kind=finding.kind.value,
                keyword=finding.keyword,
                reason=reason,
            )
        )


def _client_domains(profile: BusinessProfile) -> frozenset[str]:
    identity = profile.identity
    domains = {domain.lower().removeprefix("www.") for domain in identity.website_domains}
    if identity.website is not None:
        domains.add(identity.website.registrable_domain.lower())
    return frozenset(domains)


class RefreshEngine:
    """decides whether a business needs refreshes, then proposes the few additive edits worth making."""

    def __init__(
        self, settings: RefreshSettings, link_health: LinkHealthService, llm: LLMClient | None = None
    ) -> None:
        self.settings = settings
        self.link_health = link_health
        self.llm = llm
        self.scorer = RefreshScorer(settings)
        self.planner = OperationPlanner(settings.above_fold_chars)
        self.merger = RefreshMerger(settings)
        self.lifecycle = RefreshLifecycle(settings)
        self.impact = ImpactCalculator(settings)

    async def run(self, inputs: RefreshInputs) -> RefreshOutput:
        settings, now = self.settings, inputs.as_of
        profile = inputs.profile
        policy = policy_for(profile.freshness)
        run = _RunState(messages=[], dropped=[])
        states = build_video_states(
            inputs.own_videos, inputs.own_metrics, inputs.channel_baselines, now, settings
        )
        by_id = {state.video_id: state for state in states}
        descriptions = [video.description for video in inputs.own_videos]
        boilerplate = mine_boilerplate(
            descriptions, settings.boilerplate_min_share, settings.boilerplate_min_descriptions
        )
        matches = self._matches_by_video(inputs.topic_matches)
        confirmed: dict[str, set[str]] = defaultdict(set)
        for confirmation in inputs.confirmations:
            confirmed[confirmation.video_id].add(normalise(confirmation.keyword))
        evidence = {
            video.video_id: build_evidence(
                video,
                boilerplate,
                matches.get(video.video_id, ()),
                settings.fit_threshold,
                frozenset(confirmed.get(video.video_id, ())),
            )
            for video in inputs.own_videos
        }
        ranks = percentile_ranks(
            {
                state.video_id: state.velocity
                for state in states
                if state.is_public and state.velocity is not None
            }
        )
        links = await self.link_health.check(self._link_targets(states, evidence, profile, ranks))
        decision = BusinessEligibilityEvaluator(settings).evaluate(
            client_id=inputs.client_id,
            run_id=inputs.run_id,
            now=now,
            freshness=profile.freshness,
            states=states,
            signals=inputs.news_signals,
            buyer_gap_video_ids=self._buyer_gap_precheck(states, matches, inputs),
            defect_video_ids=self._defect_precheck(states, evidence, links.statuses, profile),
            options=inputs.options,
            last_keyword_eval_at=inputs.last_keyword_eval_at,
        )
        own_channel = inputs.own_videos[0].channel_id if inputs.own_videos else None
        ctx = RefreshContext(
            now=now,
            settings=settings,
            profile=profile,
            decision=decision,
            policy=policy,
            options=inputs.options,
            oauth_connected=inputs.oauth_connected,
            topic_refs={ref.id: ref for ref in inputs.topic_refs},
            matches_by_video=matches,
            news_signals=inputs.news_signals,
            demand=DemandEvidenceCollector(
                inputs.reference_pool,
                own_channel_id=own_channel,
                window_start=now - timedelta(days=inputs.window_days),
                settings=settings,
            ),
            link_statuses=links.statuses,
            client_domains=_client_domains(profile),
            owner_search_terms=inputs.owner_search_terms,
            states=by_id,
            channel_disclaimer=self._channel_disclaimer(boilerplate, descriptions),
        )
        gates = self._gates(states, evidence, decision, inputs, run)
        eligibility = decision.eligibility.model_copy(
            update={
                "eligible_video_count": sum(
                    gate.keyword_eligible or gate.hygiene_eligible for gate in gates.values()
                )
            }
        )
        scored = self._detect(states, gates, evidence, ctx, ranks, inputs, run)
        phraser = InsertionPhraser(
            InsertionValidator(settings, policy),
            settings,
            self.llm,
            run_id=inputs.run_id,
            client_id=inputs.client_id,
        )
        candidates, llm_calls, fallbacks = await self._plan(scored, evidence, ctx, phraser, inputs, run)
        refreshes, drafts = self._assemble(candidates, by_id, evidence, ctx, inputs, run)
        lifecycle = self.lifecycle.reconcile(
            inputs.previous_refreshes,
            videos={video.video_id: video for video in inputs.own_videos},
            signals=inputs.news_signals,
            regenerated_video_ids={refresh.video_id for refresh in refreshes},
            now=now,
        )
        impacts = self._impacts(inputs, by_id, now)
        hygiene = self._hygiene_report(states, evidence, links.statuses, profile, refreshes, settings)
        self._context_messages(decision, inputs, ctx, refreshes, run)
        return RefreshOutput(
            client_id=inputs.client_id,
            run_id=inputs.run_id,
            generated_at=now,
            needs_refresh=bool(refreshes),
            headline=self._headline(decision, refreshes, drafts, inputs),
            eligibility=eligibility,
            keyword_checks_ran=decision.keyword_enabled,
            videos=list(gates.values()),
            refreshes=refreshes,
            recommendation_drafts=drafts,
            hygiene=hygiene,
            messages=run.messages,
            alerts=lifecycle.alerts,
            status_changes=lifecycle.changes,
            impacts=impacts,
            refetch_video_ids=[
                video_id
                for video_id, gate in gates.items()
                if "description needs a refetch" in gate.skip_reasons
            ],
            dropped=run.dropped,
            counts={
                "findings": len(scored),
                "refreshes": len(refreshes),
                "drafts": len(drafts),
                "llm_calls": llm_calls,
                "llm_fallbacks": fallbacks,
                "links_probed": links.probed,
                "links_deferred": links.deferred,
            },
        )

    @staticmethod
    def _matches_by_video(matches: Sequence[TopicMatch]) -> dict[str, list[TopicMatch]]:
        grouped: dict[str, list[TopicMatch]] = defaultdict(list)
        for match in matches:
            grouped[match.video_id].append(match)
        return grouped

    def _link_targets(
        self,
        states: Sequence[VideoState],
        evidence: Mapping[str, VideoContentEvidence],
        profile: BusinessProfile,
        ranks: Mapping[str, float],
    ) -> list[tuple[str, float]]:
        # own conversion pages first, then non-footer links on high-traffic videos; footer links dedupe by url
        targets = [(action.target, 3.0) for action in profile.conversion_actions if action.medium == "web"]
        for state in states:
            if not state.is_public or state.video.view_count < self.settings.broken_link_min_views:
                continue
            structure = evidence[state.video_id].structure
            for url in structure.urls:
                footer = url.line_index in structure.boilerplate_lines
                targets.append((url.raw, ranks.get(state.video_id, 0.0) + (0.0 if footer else 1.0)))
        return targets

    def _buyer_gap_precheck(
        self, states: Sequence[VideoState], matches: Mapping[str, Sequence[TopicMatch]], inputs: RefreshInputs
    ) -> set[str]:
        refs = {ref.id: ref for ref in inputs.topic_refs}
        gaps = set()
        for state in states:
            presence = KeywordPresence(state.video.description)
            for match in matches.get(state.video_id, ()):
                ref = refs.get(match.topic_ref_id)
                if (
                    ref is not None
                    and ref.kind is TopicKind.buyer_question
                    and match.accepted
                    and match.combined_score >= self.settings.fit_threshold
                    and not presence.contains_any(topic_phrases(ref))
                ):
                    gaps.add(state.video_id)
        return gaps

    @staticmethod
    def _defect_precheck(
        states: Sequence[VideoState],
        evidence: Mapping[str, VideoContentEvidence],
        statuses: Mapping[str, LinkStatus],
        profile: BusinessProfile,
    ) -> set[str]:
        targets = [action.target for action in profile.conversion_actions if action.type in CTA_TYPES]
        defects = set()
        for state in states:
            urls = evidence[state.video_id].structure.urls
            broken = any((status := statuses.get(url.normalised)) and status.broken for url in urls)
            no_cta = bool(targets) and not any(
                same_target(url.raw, target) for url in urls for target in targets
            )
            if broken or no_cta:
                defects.add(state.video_id)
        return defects

    def _channel_disclaimer(self, keys: frozenset[str], descriptions: Sequence[str]) -> str | None:
        return next((line for line in boilerplate_lines(keys, descriptions) if DISCLAIMER.search(line)), None)

    def _gates(
        self,
        states: Sequence[VideoState],
        evidence: Mapping[str, VideoContentEvidence],
        decision: EligibilityDecision,
        inputs: RefreshInputs,
        run: _RunState,
    ) -> dict[str, VideoEligibility]:
        gate = VideoGate(self.settings)
        result = {}
        for state in states:
            item = evidence[state.video_id]
            supported = item.language in self.settings.supported_languages
            result[state.video_id] = gate.evaluate(
                state,
                now=inputs.as_of,
                fit_score=item.fit_score,
                language_supported=supported,
                decision=decision,
                history=inputs.previous_refreshes,
                options=inputs.options,
            )
            if not supported and state.is_public and decision.keyword_enabled:
                run.note(UNSUPPORTED_LANGUAGE)
        return result

    def _detect(
        self,
        states: Sequence[VideoState],
        gates: Mapping[str, VideoEligibility],
        evidence: Mapping[str, VideoContentEvidence],
        ctx: RefreshContext,
        ranks: Mapping[str, float],
        inputs: RefreshInputs,
        run: _RunState,
    ) -> list[_Scored]:
        settings = self.settings
        if ctx.decision.decision == "none":
            return []
        snoozed = self._snoozed(inputs.previous_refreshes, inputs.as_of)
        scored: list[_Scored] = []
        for state in states:
            gate = gates[state.video_id]
            if not (gate.hygiene_eligible or gate.keyword_eligible):
                continue
            for detector in trigger_registry.all().values():
                for finding in detector.detect(state, gate, evidence[state.video_id], ctx):
                    if (finding.video_id, finding.dedupe_key) in snoozed:
                        run.drop(finding, "you dismissed this recently")
                        continue
                    if not self._enough_evidence(finding, inputs.window_days, run):
                        continue
                    priority = self.scorer.priority(finding, ranks.get(state.video_id, 0.0))
                    if not self.scorer.shown(finding, priority):
                        run.drop(finding, "not worth the edit for this video right now")
                        continue
                    scored.append(_Scored(finding, priority))
        return self._cap_per_keyword(scored, ranks, settings.max_videos_per_keyword, run)

    def _enough_evidence(self, finding: Finding, window_days: int, run: _RunState) -> bool:
        # keyword cards keep the three-reference rule (D10); otherwise only the thin-data message is shown
        if not (finding.keyword_trigger and finding.proposes_text):
            return True
        demand = finding.demand_evidence
        count = len(demand.outlier_refs) if demand else 0
        if count >= self.settings.min_refs:
            return True
        keyword = finding.keyword or ""
        one = count == 1
        message = (
            f"Only {count} recent {'video' if one else 'videos'} in the last {window_days} days "
            f"{'uses' if one else 'use'} '{keyword}' and {'beats its' if one else 'beat their'} channel's usual views."
        )
        if window_days in NEXT_WINDOW:
            message += f" Widen to {NEXT_WINDOW[window_days]} days?"
        run.note(message)
        run.drop(finding, "fewer than 3 reference videos in the window")
        return False

    def _snoozed(self, history: Sequence[StoredRefresh], now: datetime) -> set[tuple[str, str]]:
        since = now - timedelta(days=self.settings.dismiss_snooze_days)
        return {
            (stored.refresh.video_id, trigger.dedupe_key)
            for stored in history
            if stored.refresh.status is RefreshStatus.dismissed and stored.updated_at >= since
            for trigger in stored.refresh.triggers
        }

    def _cap_per_keyword(
        self, scored: list[_Scored], ranks: Mapping[str, float], cap: int, run: _RunState
    ) -> list[_Scored]:
        groups: dict[str, list[_Scored]] = defaultdict(list)
        kept = []
        for item in scored:
            if item.finding.keyword_trigger and item.finding.proposes_text:
                groups[item.finding.dedupe_key].append(item)
            else:
                kept.append(item)
        for items in groups.values():
            items.sort(
                key=lambda item: ranks.get(item.finding.video_id, 0.0) * item.finding.strength, reverse=True
            )
            kept += items[:cap]
            if len(items) > cap:
                keyword = items[0].finding.keyword
                run.note(
                    f"{len(items)} videos match '{keyword}'. We picked your {cap} with the most daily views; "
                    "adding it everywhere would look like spam."
                )
                for item in items[cap:]:
                    run.drop(item.finding, f"only your top {cap} videos get this keyword")
        kept.sort(key=lambda item: self.scorer.sort_key(item.finding, item.priority), reverse=True)
        return kept

    async def _plan(
        self,
        scored: Sequence[_Scored],
        evidence: Mapping[str, VideoContentEvidence],
        ctx: RefreshContext,
        phraser: InsertionPhraser,
        inputs: RefreshInputs,
        run: _RunState,
    ) -> tuple[dict[str, list[Candidate]], int, int]:
        candidates: dict[str, list[Candidate]] = defaultdict(list)
        recent = self._recent_lines(inputs.previous_refreshes, inputs.as_of)
        jobs = []
        for item in scored:
            finding = item.finding
            if not finding.proposes_text:
                candidates[finding.video_id].append(Candidate(finding, item.priority, None))
                continue
            template = template_registry.get(finding.kind.value).render(
                finding, evidence[finding.video_id], ctx
            )
            if template is None:
                continue
            references = (
                [f"{ref.video.title}\n{ref.video.description}" for ref in finding.demand_evidence.refs]
                if finding.demand_evidence
                else []
            )
            avoid = [text for video_id, text in recent if video_id != finding.video_id][-5:]
            job = phraser.phrase(
                template,
                finding=finding,
                evidence=evidence[finding.video_id],
                references=references,
                avoid=avoid,
            )
            jobs.append((item, job))
        results: list[PhrasingResult] = await asyncio.gather(*(job for _, job in jobs))
        llm_calls = fallbacks = 0
        for (item, _), result in zip(jobs, results, strict=True):
            finding = item.finding
            llm_calls += result.llm_calls
            fallbacks += int(result.fell_back)
            if not result.accepted:
                run.drop(finding, "; ".join(result.violations))
                continue
            level = finding.claim_level or ClaimLevel.l0_navigational
            others = [text for video_id, text in recent if video_id != finding.video_id]
            if requires_variation(level) and is_repeat(result.line.text, others, self.settings):
                run.drop(finding, "the same sentence is already suggested on several videos")
                continue
            structure = evidence[finding.video_id].structure
            op = self.planner.build(finding, result.line, result.phrased_by, structure, inputs.options)
            candidates[finding.video_id].append(Candidate(finding, item.priority, op))
            if requires_variation(level):
                recent.append((finding.video_id, op.text))
        return candidates, llm_calls, fallbacks

    @staticmethod
    def _recent_lines(history: Sequence[StoredRefresh], now: datetime) -> list[tuple[str, str]]:
        since = now - timedelta(days=30)
        return [
            (stored.refresh.video_id, op.text)
            for stored in history
            if stored.refresh.generated_at >= since
            for op in stored.refresh.operations
            if requires_variation(op.claim_level)
        ]

    def _assemble(
        self,
        candidates: Mapping[str, Sequence[Candidate]],
        states: Mapping[str, VideoState],
        evidence: Mapping[str, VideoContentEvidence],
        ctx: RefreshContext,
        inputs: RefreshInputs,
        run: _RunState,
    ) -> tuple[list[DescriptionRefresh], list[RecommendationDraft]]:
        merged: list[tuple[float, float, str, MergeResult]] = []
        for video_id, items in candidates.items():
            result = self.merger.merge(states[video_id].video, items)
            for finding, reason in result.dropped:
                run.drop(finding, reason)
            if not result.operations and not result.advisories:
                continue
            if result.resulting_bytes > DESCRIPTION_BYTE_LIMIT:
                for finding in result.findings:
                    run.drop(finding, "the description is already over YouTube's 5000-byte limit")
                continue
            top = max(items, key=lambda item: item.priority)
            merged.append((top.priority, top.finding.urgency, video_id, result))
        merged.sort(key=lambda item: (item[0], item[1]), reverse=True)
        cap = self.settings.max_refreshes_per_run
        for _, _, _, result in merged[cap:]:
            for finding in result.findings:
                run.drop(finding, f"at most {cap} refresh suggestions per run")
        refreshes, drafts = [], []
        for priority, _, video_id, result in merged[:cap]:
            refresh, keyword_findings = self._build_refresh(
                video_id, priority, result, states[video_id], evidence[video_id], ctx, inputs
            )
            refreshes.append(refresh)
            if keyword_findings:
                draft = self._draft(
                    refresh, keyword_findings, result, states[video_id], evidence[video_id], ctx, inputs
                )
                if draft is not None:
                    drafts.append(draft)
        return refreshes, drafts

    def _build_refresh(
        self,
        video_id: str,
        priority: float,
        result: MergeResult,
        state: VideoState,
        evidence: VideoContentEvidence,
        ctx: RefreshContext,
        inputs: RefreshInputs,
    ) -> tuple[DescriptionRefresh, list[Finding]]:
        settings, now = self.settings, inputs.as_of
        used_triggers = {trigger_id for op in result.operations for trigger_id in op.trigger_ids}
        keyword_findings = [
            finding
            for finding in result.findings
            if finding.keyword_trigger
            and finding.trigger_id in used_triggers
            and finding.demand_evidence is not None
        ]
        if keyword_findings:
            best = max(keyword_findings, key=lambda finding: len(finding.demand_evidence.outlier_refs))
            confidence = keyword_confidence(best.demand_evidence, best.strength, state.velocity_basis)
        else:
            confidence = rule_based_confidence()
        comparison_age = state.metrics.comparison_age_days if state.metrics else 7
        effect, _ = expected_effect(result.findings, inputs.past_impacts, settings, comparison_age)
        advisories = list(result.advisories) + self._description_advisories(evidence)
        review = ctx.policy.review_required or any(
            op.claim_level is ClaimLevel.l1_context for op in result.operations
        )
        refresh = DescriptionRefresh(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"refresh:{inputs.client_id}:{video_id}:{inputs.run_id}")),
            client_id=inputs.client_id,
            video_id=video_id,
            run_id=inputs.run_id,
            triggers=[finding.to_trigger() for finding in result.findings],
            operations=list(result.operations),
            advisories=advisories,
            suggested_tags=self._suggested_tags(keyword_findings, state),
            keywords_added=list(dict.fromkeys(k for op in result.operations for k in op.keywords_added)),
            inserted_bytes=result.inserted_bytes,
            resulting_bytes=result.resulting_bytes,
            expected_effect=effect,
            confidence=confidence,
            priority=priority,
            expires_at=self._expiry(result.findings, ctx),
            status=RefreshStatus.proposed,
            description_hash_at_generation=state.video.description_hash,
            review_required=review,
            generated_at=now,
        )
        return refresh, keyword_findings

    @staticmethod
    def _description_advisories(evidence: VideoContentEvidence) -> list[Advisory]:
        structure = evidence.structure
        if structure.is_empty:
            message = "This video has no description. Start with one line about what it covers."
        elif structure.only_boilerplate:
            message = "Your description only has the standard footer. Add a line about this video above it."
        else:
            return []
        return [Advisory(kind="manual_review", message=message, risk="low")]

    @staticmethod
    def _suggested_tags(findings: Sequence[Finding], state: VideoState) -> list[str]:
        # aliases only, and never when the keyword is already tagged; tags play a minimal role in discovery
        tags = KeywordPresence(" ".join(state.video.tags))
        suggestions = []
        for finding in findings:
            if finding.keyword and not tags.contains(finding.keyword):
                suggestions += [alias for alias in finding.aliases if not tags.contains(alias)]
        return list(dict.fromkeys(suggestions))

    def _expiry(self, findings: Sequence[Finding], ctx: RefreshContext) -> datetime:
        settings, now = self.settings, ctx.now
        signals = {signal.id: signal for signal in ctx.news_signals}
        candidates = []
        for finding in findings:
            if finding.news_signal_id is not None:
                expiry = now + timedelta(days=settings.news_expiry_days)
                signal = signals.get(finding.news_signal_id)
                candidates.append(min(expiry, signal.expires_at) if signal else expiry)
            elif finding.keyword_trigger:
                candidates.append(now + timedelta(days=settings.keyword_expiry_days))
            else:
                candidates.append(now + timedelta(days=settings.hygiene_expiry_days))
        return min(candidates, default=now + timedelta(days=settings.hygiene_expiry_days))

    def _draft(
        self,
        refresh: DescriptionRefresh,
        findings: Sequence[Finding],
        result: MergeResult,
        state: VideoState,
        evidence: VideoContentEvidence,
        ctx: RefreshContext,
        inputs: RefreshInputs,
    ) -> RecommendationDraft | None:
        references = {
            ref.video.video_id: ref
            for finding in findings
            if finding.demand_evidence
            for ref in finding.demand_evidence.outlier_refs
        }
        comparison_age = state.metrics.comparison_age_days if state.metrics else 7
        _, expected_range = expected_effect(
            result.findings, inputs.past_impacts, self.settings, comparison_age
        )
        topics = [
            ctx.topic_refs[ref_id] for ref_id in evidence.covered_topic_refs if ref_id in ctx.topic_refs
        ]
        draft = to_recommendation_draft(
            refresh,
            findings=result.findings,
            references=list(references.values()),
            velocity=state.velocity,
            topic_refs=topics,
            expected_range=expected_range,
            window_days=inputs.window_days,
            as_of=inputs.as_of,
        )
        gate_results = [gate.check(draft) for gate in (AdditiveDiffOnly(), MinimalEdit())]
        if any(result.action is GateAction.suppress for result in gate_results):
            return None
        return draft.model_copy(update={"gate_results": gate_results})

    def _impacts(
        self, inputs: RefreshInputs, states: Mapping[str, VideoState], now: datetime
    ) -> list[RefreshImpact]:
        measured = {item.impact.refresh_id for item in inputs.past_impacts}
        return [
            self.impact.compute(
                stored,
                states=states,
                snapshots=inputs.snapshots,
                history=inputs.previous_refreshes,
                owner_search_daily=inputs.owner_search_daily_views,
                now=now,
            )
            for stored in inputs.previous_refreshes
            if stored.refresh.id not in measured and self.impact.due(stored, now)
        ]

    def _hygiene_report(
        self,
        states: Sequence[VideoState],
        evidence: Mapping[str, VideoContentEvidence],
        statuses: Mapping[str, LinkStatus],
        profile: BusinessProfile,
        refreshes: Sequence[DescriptionRefresh],
        settings: RefreshSettings,
    ) -> HygieneReport:
        public = [state for state in states if state.is_public]
        booking = [action.target for action in profile.conversion_actions if action.type in CTA_TYPES]
        with_booking = sum(
            any(
                same_target(url.raw, target)
                for url in evidence[state.video_id].structure.urls
                for target in booking
            )
            for state in public
        )
        long_videos = [
            state
            for state in public
            if not state.maybe_short and (state.video.duration_s or 0) >= settings.chapters_min_duration_s
        ]
        with_chapters = sum(evidence[state.video_id].structure.chapters_valid for state in long_videos)
        malformed = dead = 0
        for state in public:
            classes = {
                status.status_class
                for url in evidence[state.video_id].structure.urls
                if (status := statuses.get(url.normalised)) is not None
            }
            malformed += LinkStatusClass.malformed in classes
            dead += bool(classes & {LinkStatusClass.dead, LinkStatusClass.soft_404})
        summary = []
        if booking:
            signup_only = all(
                action.type in ("signup", "free_trial")
                for action in profile.conversion_actions
                if action.type in CTA_TYPES
            )
            page = "sign-up page" if signup_only else "booking page"
            summary.append(f"{with_booking} of {len(public)} recent descriptions link to your {page}")
        if long_videos:
            summary.append(f"{with_chapters} of {len(long_videos)} long videos have chapters")
        if malformed:
            summary.append(f"{malformed} {'video has' if malformed == 1 else 'videos have'} malformed links")
        if dead:
            summary.append(f"{dead} {'video has' if dead == 1 else 'videos have'} dead links")
        return HygieneReport(
            videos_checked=len(public),
            with_booking_link=with_booking,
            long_videos=len(long_videos),
            with_valid_chapters=with_chapters,
            malformed_links=malformed,
            dead_links=dead,
            refresh_ids=[
                refresh.id for refresh in refreshes if refresh.confidence.basis is ConfidenceBasis.rule_based
            ],
            summary=summary,
        )

    def _context_messages(
        self,
        decision: EligibilityDecision,
        inputs: RefreshInputs,
        ctx: RefreshContext,
        refreshes: Sequence[DescriptionRefresh],
        run: _RunState,
    ) -> None:
        profile = inputs.profile
        if decision.provisional:
            run.note("Based on lifetime averages; we'll confirm once we have 7 days of data.")
        web_actions = [action for action in profile.conversion_actions if action.type in CTA_TYPES]
        if not web_actions:
            run.note(NO_ACTIONS)
        for action in web_actions:
            status = ctx.link_statuses.get(normalise_url(action.target))
            if status is not None and status.broken:
                run.note(
                    f"Your '{action.label}' page ({action.target}) looks broken, so we didn't suggest it."
                )
        if any(refresh.review_required for refresh in refreshes) and ctx.policy.domain_kind == "healthcare":
            run.note(REVIEW_NOTE)
        if refreshes and not inputs.oauth_connected:
            run.note(PUBLIC_MODE_FOOTER)

    @staticmethod
    def _headline(
        decision: EligibilityDecision,
        refreshes: Sequence[DescriptionRefresh],
        drafts: Sequence[RecommendationDraft],
        inputs: RefreshInputs,
    ) -> str:
        if inputs.options.mode == "off":
            return "Description refresh is turned off in Settings."
        if decision.decision == "none":
            return (
                "No refresh needed right now: your videos aren't earning steady views and there's no fresh news "
                "in your niche."
            )
        hygiene = len(refreshes) - len(drafts)
        if drafts:
            fixes = f", plus {hygiene} description fixes" if hygiene else ""
            plural = "s" if len(drafts) != 1 else ""
            return f"{len(drafts)} video{plural} could use a small keyword update{fixes}."
        if hygiene:
            plural = "es" if hygiene != 1 else ""
            return f"No keyword refresh needed. {hygiene} description fix{plural} found (links, booking, chapters)."
        if decision.decision == "hygiene_only":
            return "No refresh needed. Your descriptions look healthy. Nothing to change this month."
        return "No refresh needed right now. Your descriptions already cover what people search for."
