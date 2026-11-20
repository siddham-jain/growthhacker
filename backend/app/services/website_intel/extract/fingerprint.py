import hashlib
import re

_WORD = re.compile(r"\w+", re.UNICODE)
_MASK_64 = (1 << 64) - 1
SHINGLE_SIZE = 3


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _shingle_hash(shingle: str) -> int:
    return int.from_bytes(hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")


def simhash64(text: str) -> int:
    """64-bit simhash over word 3-shingles, returned signed so it fits a postgres bigint."""
    words = [word.casefold() for word in _WORD.findall(text)]
    if not words:
        return 0
    if len(words) < SHINGLE_SIZE:
        shingles = [" ".join(words)]
    else:
        shingles = [" ".join(words[i : i + SHINGLE_SIZE]) for i in range(len(words) - SHINGLE_SIZE + 1)]
    weights = [0] * 64
    for shingle in shingles:
        value = _shingle_hash(shingle)
        for bit in range(64):
            weights[bit] += 1 if value >> bit & 1 else -1
    unsigned = sum(1 << bit for bit, weight in enumerate(weights) if weight > 0)
    return unsigned - (1 << 64) if unsigned >= 1 << 63 else unsigned


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & _MASK_64).bit_count()
