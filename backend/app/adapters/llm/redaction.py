import re

# comments are the only free text from viewers; authors are dropped upstream, contact details masked here
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://\S+|www\.\S+", re.I), "[url]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"(?<![\w@])@[\w.]{2,}"), "[mention]"),
    (re.compile(r"(?:\+?91[\s-]?)?(?<!\d)[6-9]\d{4}[\s-]?\d{5}(?!\d)"), "[phone]"),
    (re.compile(r"(?<![\w])\+?\d[\d\s().-]{8,}\d(?![\w])"), "[phone]"),
)


def redact_pii(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text
