import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence

MIN_PAGES = 8
MIN_SHARE = 0.4
MIN_PAGE_COUNT = 5
_PUNCTUATION_EDGES = re.compile(r"^[\W_]+|[\W_]+$")
_MARKDOWN_PREFIX = re.compile(r"^(?:#{1,6}\s+|[*+-]\s+|\d+\.\s+)")


def _block_key(block: str) -> str | None:
    normalised = _PUNCTUATION_EDGES.sub("", _MARKDOWN_PREFIX.sub("", block.strip())).casefold()
    normalised = " ".join(normalised.split())
    if not normalised:
        return None
    return hashlib.blake2b(normalised.encode("utf-8"), digest_size=12).hexdigest()


class CrossPageBoilerplateRemover:
    """drops text blocks repeated across many pages of one site (plan 03 §5.7.5)."""

    def remove(self, texts: Sequence[str]) -> list[str]:
        if len(texts) < MIN_PAGES:
            return list(texts)
        page_blocks = [text.split("\n") for text in texts]
        counts: Counter[str] = Counter()
        for blocks in page_blocks:
            counts.update({key for block in blocks if (key := _block_key(block)) is not None})
        threshold = max(MIN_PAGE_COUNT, math.ceil(MIN_SHARE * len(texts)))
        boilerplate = {key for key, count in counts.items() if count >= threshold}
        return [
            "\n".join(block for block in blocks if _block_key(block) not in boilerplate).strip()
            for blocks in page_blocks
        ]
