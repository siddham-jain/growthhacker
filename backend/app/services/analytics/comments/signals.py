from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from difflib import SequenceMatcher

import regex

from app.domain.analytics import CommentSignals, IntentClass
from app.domain.topics import TopicRef
from app.domain.youtube import CommentRecord, VideoRecord
from app.services.analytics.comments.lexicon import IntentLexicon
from app.services.analytics.settings import CommentSettings
from app.services.analytics.traits.title import TitleLexicon, is_question

URL = regex.compile(r"https?://\S+|www\.\S+", regex.IGNORECASE)
EMAIL = regex.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = regex.compile(r"(?<!\d)\+?\d[\d\s-]{7,14}\d(?!\d)")
HANDLE = regex.compile(r"(?<![\w@])@[\w.\-]{2,30}")
EMOJI = regex.compile(r"\p{Extended_Pictographic}")
LETTER = regex.compile(r"[\p{L}]")
TOKEN = regex.compile(r"[\p{L}\p{M}\p{N}]+")
NOT_ALNUM = regex.compile(r"[^\p{L}\p{M}\p{N}]+")
REDACTED = "[redacted]"


def normalise_comment(text: str) -> str:
    return NOT_ALNUM.sub(" ", text.casefold()).strip()


class PiiRedactor:
    def redact(self, text: str) -> str:
        for pattern in (URL, EMAIL, PHONE, HANDLE):
            text = pattern.sub(REDACTED, text)
        return text


class SpamFilter:
    def __init__(self, lexicon: IntentLexicon, settings: CommentSettings) -> None:
        self.lexicon = lexicon
        self.settings = settings

    def repeated_texts(self, comments_by_video: Mapping[str, Sequence[CommentRecord]]) -> frozenset[str]:
        # the same text across several videos of the scope is copy-paste spam
        videos_per_text: Counter[str] = Counter()
        for comments in comments_by_video.values():
            for text in {normalise_comment(c.text) for c in comments}:
                if text:
                    videos_per_text[text] += 1
        return frozenset(t for t, n in videos_per_text.items() if n >= self.settings.spam_repeat_videos)

    def eligible(self, comment: CommentRecord, repeated: frozenset[str]) -> bool:
        text = comment.text
        if comment.is_channel_owner_reply:
            return False
        if len(LETTER.findall(EMOJI.sub("", text))) < self.settings.min_letters:
            return False
        if URL.search(text) or self.lexicon.is_spam(text):
            return False
        return normalise_comment(text) not in repeated


def _term_present(term: str, text: str, tokens: Sequence[str], threshold: float) -> bool:
    folded = term.casefold()
    if folded in text:
        return True
    words = TOKEN.findall(folded)
    return bool(words) and all(
        any(SequenceMatcher(None, word, token).ratio() >= threshold for token in tokens) for word in words
    )


class CommentSignalExtractor:
    """counts buyer intent in sampled comments; text is processed in memory and never stored."""

    def __init__(
        self,
        settings: CommentSettings,
        lexicon: IntentLexicon,
        buyer_questions: Sequence[TopicRef],
        location_terms: Sequence[str],
        excerpts_enabled: bool,
    ) -> None:
        self.settings = settings
        self.lexicon = lexicon
        self.titles = TitleLexicon()
        self.buyer_questions = [q for q in buyer_questions if q.core_entity_terms]
        self.location_terms = tuple(location_terms)
        self.excerpts_enabled = excerpts_enabled
        self.redactor = PiiRedactor()
        self.spam = SpamFilter(lexicon, settings)

    def extract(
        self, video: VideoRecord, comments: Sequence[CommentRecord], repeated: frozenset[str], as_of: datetime
    ) -> CommentSignals:
        sampled = sorted(comments, key=lambda c: (c.relevance_rank, c.comment_id))[
            : self.settings.max_comments_per_video
        ]
        eligible = [c for c in sampled if self.spam.eligible(c, repeated)]
        intent_counts = dict.fromkeys(IntentClass, 0)
        questions = unanswered = buyer_intent = owner_replies = 0
        matched: Counter[str] = Counter()
        excerpts: list[str] = []
        for comment in eligible:
            text = comment.text
            folded = text.casefold()
            tokens = TOKEN.findall(folded)
            classes = self.lexicon.classes(text, self.location_terms)
            for intent in classes:
                intent_counts[intent] += 1
            asked = is_question(text, self.titles)
            questions += asked
            unanswered += asked and (comment.reply_count == 0 or not comment.owner_replied)
            owner_replies += comment.owner_replied
            if classes:
                buyer_intent += 1
                if self.excerpts_enabled and len(excerpts) < self.settings.max_excerpts:
                    excerpts.append(self.redactor.redact(text)[: self.settings.excerpt_max_chars])
            for question in self.buyer_questions:
                threshold = self.settings.buyer_question_similarity
                if all(_term_present(t, folded, tokens, threshold) for t in question.core_entity_terms):
                    matched[question.id] += 1
        n = len(eligible)
        sufficient = n >= self.settings.min_comments_for_signals and video.comments_state != "disabled"

        def share(count: int) -> float | None:
            return count / n if sufficient else None

        return CommentSignals(
            video_id=video.video_id,
            as_of=as_of,
            comment_count_api=video.comment_count,
            sampled_n=len(sampled),
            eligible_n=n,
            sample_coverage=len(sampled) / video.comment_count if video.comment_count else None,
            question_n=questions,
            question_share=share(questions),
            intent_counts=intent_counts,
            buyer_intent_n=buyer_intent,
            buyer_intent_share=share(buyer_intent),
            unanswered_question_n=unanswered,
            unanswered_question_share=unanswered / questions if sufficient and questions else None,
            owner_reply_visible_n=owner_replies,
            lexicon_version=self.lexicon.version,
            sufficient=sufficient,
            matched_buyer_questions=sorted(matched.items()),
            excerpts=excerpts,
        )
