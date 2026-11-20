from collections.abc import Iterable, Sequence

import py3langid
import regex

from app.core.config import RefreshSettings
from app.domain.refresh import MAX_OPERATION_CHARS, ClaimLevel, InsertionKind
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.links.urls import URL
from app.services.refresh.ops.templates import TemplateLine
from app.services.refresh.policy.claims import asserted_level
from app.services.refresh.policy.domain import DomainPolicy
from app.services.refresh.text import (
    HASHTAG,
    KeywordPresence,
    content_tokens,
    jaccard,
    numbers_in,
    shingles,
)
from app.services.refresh.triggers.base import Finding

LIST_SEPARATORS = regex.compile(r"[,|;/•·]")
MONTH_TOKENS = frozenset(
    [
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ]
)
MIN_LANGUAGE_CHECK_CHARS = 20
MIN_REWRITE_TOKENS = 3
COPY_SHINGLE = 6


def _without_hashtags(text: str) -> str:
    return HASHTAG.sub(" ", text)


class InsertionValidator:
    """deterministic truth, policy and spam checks; anything that fails is retried, templated or dropped."""

    def __init__(self, settings: RefreshSettings, policy: DomainPolicy) -> None:
        self.settings = settings
        self.policy = policy

    def validate(
        self,
        line: TemplateLine,
        *,
        finding: Finding,
        evidence: VideoContentEvidence,
        phrased_by: str,
        template_text: str,
        references: Sequence[str] = (),
    ) -> list[str]:
        text = line.text
        problems = self._shape(line)
        if line.kind is InsertionKind.link_fix or problems:
            return problems
        description = evidence.structure.text
        problems += self._links(text, line.allowed_urls)
        problems += self._keywords(text, line.keywords_added, finding.aliases)
        problems += self._vocabulary(text, line, finding, evidence)
        problems += self._numbers(text, line, finding, evidence, template_text)
        problems += self._claim_level(text, finding)
        problems += self.policy.violations(text, description)
        problems += self._stuffing(text, line, evidence)
        if phrased_by == "llm":
            problems += self._language(text, evidence)
        problems += self._copying(text, references, evidence, finding.claim_level, line.keywords_added)
        return problems

    @staticmethod
    def _shape(line: TemplateLine) -> list[str]:
        text = line.text
        problems = []
        if not text.strip():
            problems.append("empty line")
        if "\n" in text or "\r" in text:
            problems.append("must be a single line, not a rewrite")
        if "<" in text or ">" in text:
            problems.append("contains < or >, which YouTube rejects")
        if len(text) > MAX_OPERATION_CHARS:
            problems.append(f"longer than {MAX_OPERATION_CHARS} characters")
        if line.kind is InsertionKind.link_fix:
            if not (line.old_text and URL.fullmatch(line.old_text)):
                problems.append("a link fix may only replace a link")
            if not URL.fullmatch(text) or text not in line.allowed_urls:
                problems.append("a link fix may only insert the repaired link")
        return problems

    @staticmethod
    def _links(text: str, allowed: frozenset[str]) -> list[str]:
        return [f"adds a link we did not provide: {url}" for url in URL.findall(text) if url not in allowed]

    @staticmethod
    def _keywords(text: str, keywords: Iterable[str], aliases: Iterable[str]) -> list[str]:
        presence = KeywordPresence(text)
        return [
            f"missing the keyword '{keyword}'"
            for keyword in keywords
            if not presence.contains(keyword) and not presence.contains_any(aliases)
        ]

    @staticmethod
    def _vocabulary(
        text: str, line: TemplateLine, finding: Finding, evidence: VideoContentEvidence
    ) -> list[str]:
        allowed = set(evidence.covered_tokens) | set(line.vocabulary) | MONTH_TOKENS
        for phrase in (*line.keywords_added, *finding.aliases):
            allowed |= set(content_tokens(phrase))
        unknown = [token for token in content_tokens(text) if token not in allowed and not token.isdigit()]
        if unknown:
            return [f"uses words the video's own metadata doesn't support: {', '.join(sorted(set(unknown)))}"]
        return []

    @staticmethod
    def _numbers(
        text: str, line: TemplateLine, finding: Finding, evidence: VideoContentEvidence, template_text: str
    ) -> list[str]:
        structure = evidence.structure
        content = [
            body for index, body in enumerate(structure.lines) if index not in structure.boilerplate_lines
        ]
        allowed = numbers_in(template_text) | numbers_in("\n".join(content))
        for phrase in (*line.keywords_added, *finding.aliases):
            allowed |= numbers_in(phrase)
        extra = sorted(numbers_in(text) - allowed)
        return [f"adds numbers not in the evidence: {', '.join(extra)}"] if extra else []

    def _claim_level(self, text: str, finding: Finding) -> list[str]:
        planned = finding.claim_level
        if planned is None:
            return ["no claim level was planned"]
        problems = []
        asserted = asserted_level(text)
        if asserted is not planned:
            problems.append(
                f"claims more than the evidence allows ({asserted.value}, allowed {planned.value})"
            )
        if not self.policy.permits_claim(finding.kind.value, planned):
            problems.append(f"{planned.value} wording is not allowed for {self.policy.domain_kind} channels")
        return problems

    def _stuffing(self, text: str, line: TemplateLine, evidence: VideoContentEvidence) -> list[str]:
        settings = self.settings
        problems = []
        combined = KeywordPresence(
            _without_hashtags(evidence.structure.text) + "\n" + _without_hashtags(text)
        )
        for keyword in line.keywords_added:
            if combined.count(keyword) > settings.keyword_max_occurrences:
                problems.append(
                    f"'{keyword}' would appear more than {settings.keyword_max_occurrences} times"
                )
        segments = [segment for segment in LIST_SEPARATORS.split(URL.sub(" ", text)) if segment.strip()]
        if len(segments) >= 4 and all(len(segment.split()) <= 3 for segment in segments):
            problems.append("reads like a keyword list")
        total = len(evidence.structure.hashtags) + len(HASHTAG.findall(text))
        if total > settings.max_hashtags_total:
            problems.append(f"more than {settings.max_hashtags_total} hashtags in total")
        return problems

    @staticmethod
    def _language(text: str, evidence: VideoContentEvidence) -> list[str]:
        if len(text) < MIN_LANGUAGE_CHECK_CHARS:
            return []
        language, _ = py3langid.classify(URL.sub(" ", text))
        return (
            []
            if language == evidence.language
            else [f"written in {language}, description is {evidence.language}"]
        )

    def _copying(
        self,
        text: str,
        references: Sequence[str],
        evidence: VideoContentEvidence,
        level: ClaimLevel | None,
        keywords: Sequence[str],
    ) -> list[str]:
        problems = []
        # the keyword itself is shared with references by design; only the wording around it must differ
        line_shingles = shingles(text, COPY_SHINGLE).difference(
            *(shingles(keyword, COPY_SHINGLE) for keyword in keywords)
        )
        if line_shingles and any(
            line_shingles & shingles(reference, COPY_SHINGLE) for reference in references
        ):
            problems.append("copies wording from a reference video")
        tokens = set(content_tokens(text))
        for existing in evidence.structure.lines:
            if not existing.strip():
                continue
            if existing.strip() == text.strip():
                problems.append("repeats an existing line of the description")
                break
            # a line that restates most of an existing line plus a keyword is a rewrite, not an addition
            existing_tokens = set(content_tokens(existing))
            if level is not ClaimLevel.l0_navigational and len(existing_tokens) >= MIN_REWRITE_TOKENS:
                contained = len(existing_tokens & tokens) / len(existing_tokens)
                if contained >= self.settings.repeat_jaccard:
                    problems.append("rewrites an existing line instead of adding a new one")
                    break
        return problems


def is_repeat(text: str, recent: Sequence[str], settings: RefreshSettings) -> bool:
    # the same sentence on many videos reads as mass-produced metadata
    tokens = content_tokens(text)
    similar = sum(jaccard(tokens, content_tokens(other)) >= settings.repeat_jaccard for other in recent)
    return similar >= settings.max_repeats_per_30d


def requires_variation(level: ClaimLevel) -> bool:
    return level is not ClaimLevel.l0_navigational
