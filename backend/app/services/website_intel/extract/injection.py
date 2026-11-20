import re
from dataclasses import dataclass

REPLACEMENT = "[removed: instruction-like text]"
MAX_SPAN_CHARS = 300
_SENTENCE_END = re.compile(r"[.!?।\n]")

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:of\s+the\s+)?(?:previous|prior|above|earlier|preceding)\s+"
        r"(?:instructions?|prompts?|messages?|rules?)",
        r"\bdisregard\s+(?:\w+\s+){0,4}(?:instructions?|prompts?|rules?)",
        r"\bforget\s+(?:all\s+|your\s+)?(?:previous|prior|earlier)\s+(?:instructions?|prompts?)",
        r"\byou\s+are\s+(?:now\s+)?(?:an?|the)\s+(?:ai|assistant|chatgpt|claude|gpt|language\s+model|llm)\b",
        r"\bsystem\s+prompt\b",
        r"\bdeveloper\s+(?:message|mode|prompt)\b",
        r"<\|?\s*(?:im_start|im_end|system)\s*\|?>",
        r"#{2,}\s*(?:instruction|instructions|system)\b",
        r"\bdo\s+not\s+(?:tell|reveal|disclose)\b",
        r"\brespond\s+only\s+with\b",
        r"\bas\s+an\s+ai\b",
        r"\b(?:attention|note|message|instructions?)\s+(?:to|for)\s+(?:all\s+)?(?:llms?|ai\s+(?:agents?|assistants?|"
        r"models?|crawlers?|systems?)|language\s+models?|chatbots?|crawlers?|bots?)\b",
        r"\bif\s+you\s+are\s+an?\s+(?:ai|llm|language\s+model|assistant|crawler|bot)\b",
        r"[A-Za-z0-9+/]{200,}={0,2}",
        r"(?:पिछले|पूर्व|ऊपर\s+के)\s+(?:सभी\s+)?निर्देश(?:ों)?\s+(?:को\s+)?(?:अनदेखा|नज़रअंदाज़|नजरअंदाज|भूल)",
        r"निर्देशों\s+को\s+(?:अनदेखा|नज़रअंदाज़|नजरअंदाज)\s+कर",
        r"सिस्टम\s+प्रॉम्प्ट",
        r"\bpichhle\s+(?:sabhi\s+)?nirdesh(?:on)?\s+(?:ko\s+)?(?:ignore|andekha|bhool)",
    )
)


@dataclass(frozen=True)
class ScanResult:
    text: str
    flags: int


def _spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            end = match.end()
            sentence_end = _SENTENCE_END.search(text, end)
            if sentence_end is not None and sentence_end.end() - match.start() <= MAX_SPAN_CHARS:
                end = sentence_end.end()
            spans.append((match.start(), end))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class InjectionScanner:
    """masks and counts instruction-like spans (plan 03 §5.11.3); a signal, the llm data fence is the defence."""

    def scan(self, text: str) -> ScanResult:
        spans = _spans(text)
        if not spans:
            return ScanResult(text=text, flags=0)
        pieces: list[str] = []
        cursor = 0
        for start, end in spans:
            pieces.append(text[cursor:start])
            pieces.append(REPLACEMENT + (text[end - 1] if text[end - 1] == "\n" else ""))
            cursor = end
        pieces.append(text[cursor:])
        return ScanResult(text="".join(pieces), flags=len(spans))


def contains_instruction(text: str) -> bool:
    """drops model outputs that echo instructions; a signal, not the input defence."""
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)
