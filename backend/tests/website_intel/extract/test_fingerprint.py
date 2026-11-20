from app.services.website_intel.extract.fingerprint import content_hash, hamming, simhash64

BASE = " ".join(f"word{i}" for i in range(200))


def test_content_hash_is_sha256_hex():
    assert content_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_simhash_is_signed_64_bit_and_stable():
    value = simhash64(BASE)
    assert -(1 << 63) <= value < (1 << 63)
    assert value == simhash64(BASE.upper())


def test_near_duplicates_are_close_and_different_texts_far():
    near = BASE.replace("word100", "changed")
    other = " ".join(f"token{i}" for i in range(200))
    assert hamming(simhash64(BASE), simhash64(near)) <= 3
    assert hamming(simhash64(BASE), simhash64(other)) > 10


def test_empty_and_short_text():
    assert simhash64("") == 0
    assert simhash64("two words") == simhash64("TWO WORDS")
