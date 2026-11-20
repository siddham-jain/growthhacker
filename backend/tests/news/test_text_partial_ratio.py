import random
from types import SimpleNamespace

from app.services.news.prefilter import lexical_score
from app.services.news.text import _ratio, contains_phrase, normalize, partial_ratio


def naive_partial_ratio(needle: str, haystack: str) -> float:
    short, long = normalize(needle), normalize(haystack)
    if not short or not long:
        return 0.0
    if len(short) > len(long):
        short, long = long, short
    width = len(short)
    best = 0.0
    for start in range(0, len(long) - width + 1):
        best = max(best, _ratio(short, long[start : start + width]))
        if best == 100.0:
            break
    return best


def test_pruned_partial_ratio_equals_the_full_scan() -> None:
    rng = random.Random(7)
    alphabet = "abcde fgh"
    cases = [
        ("cardiology", "Apollo opens a new cardiology wing in Chennai"),
        ("knee replacement", "robotic knee surgery outcomes improve"),
        ("aaa", "aaaa"),
        ("", "anything"),
        ("longer needle than hay", "short"),
    ]
    for _ in range(400):
        needle = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
        haystack = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 80)))
        cases.append((needle, haystack))
    for needle, haystack in cases:
        assert partial_ratio(needle, haystack) == naive_partial_ratio(needle, haystack), (needle, haystack)


def naive_lexical(terms: list[str], items: list[SimpleNamespace]) -> float:
    def score(term: str, item: SimpleNamespace) -> float:
        if contains_phrase(item.title, term):
            return 1.0
        if contains_phrase(item.snippet, term):
            return 0.6
        return naive_partial_ratio(term, item.title) / 100 * 0.4

    return max((score(term, item) for term in terms for item in items), default=0.0)


def test_lexical_score_equals_scoring_every_pair() -> None:
    rng = random.Random(11)
    words = [
        "apollo",
        "hospital",
        "cardiology",
        "heart",
        "surgery",
        "cancer",
        "care",
        "chennai",
        "robotic",
        "knee",
        "clinic",
        "new",
        "wing",
    ]

    def text(count: int) -> str:
        return " ".join(rng.choice(words) for _ in range(count))

    for _ in range(150):
        terms = [text(rng.randint(1, 3)) for _ in range(rng.randint(0, 6))] + ["cardio", "Knee-replacement"]
        items = [
            SimpleNamespace(title=text(rng.randint(0, 12)), snippet=text(rng.randint(0, 20)))
            for _ in range(3)
        ]
        rng.shuffle(terms)
        assert lexical_score(terms, items) == naive_lexical(terms, items)
