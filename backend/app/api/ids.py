from uuid import UUID

from app.api.errors import NotFound

# uuidv7 keys inside, prefixed opaque strings at the api boundary (99 C84); services use these too
WORKSPACE = "ws"
CLIENT = "cl"
RUN = "run"
USER = "usr"
COMPETITOR = "cmp"
KEYWORD = "kw"
BUYER_QUESTION = "bq"
NEWS = "news"
REFRESH = "rfs"


def encode(prefix: str, value: UUID) -> str:
    return f"{prefix}_{value.hex}"


def encode_optional(prefix: str, value: UUID | None) -> str | None:
    return None if value is None else encode(prefix, value)


def decode(prefix: str, value: str) -> UUID:
    # an id of the wrong kind or shape is simply not found, never a 422 that leaks the format
    head, separator, tail = value.partition("_")
    if separator != "_" or head != prefix or len(tail) != 32:
        raise NotFound(f"no such {prefix} id")
    try:
        return UUID(hex=tail)
    except ValueError:
        raise NotFound(f"no such {prefix} id") from None
