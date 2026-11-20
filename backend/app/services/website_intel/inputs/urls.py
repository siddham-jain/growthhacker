import hashlib
import ipaddress
import posixpath
import re
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

import tldextract

MAX_INPUT_CHARS = 2048
TRACKING_PARAMS = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "dclid",
        "yclid",
        "_ga",
        "_gl",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "igshid",
        "si",
    }
)
INDEX_DOCUMENTS = ("index.html", "index.htm", "index.php")
_WHITESPACE_OR_CONTROL = re.compile(r"[\s\x00-\x1f\x7f]")
_SAFE_PATH = "/:@!$&'()*+,;=-._~%"
_SAFE_QUERY = "/:@!$'()*+,;-._~%?"


class InvalidUrlError(ValueError):
    def __init__(self, message: str, *, user_message: str) -> None:
        super().__init__(message)
        self.user_message = user_message


BAD_ADDRESS = "That doesn't look like a website address. Try e.g. yourclinic.com."


@lru_cache(maxsize=1)
def _extractor() -> tldextract.TLDExtract:
    # bundled public suffix snapshot only; never fetched at runtime
    return tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True, cache_dir=None)


def registrable_domain(host: str) -> str | None:
    parts = _extractor()(host)
    if not parts.suffix or not parts.domain:
        return None
    return f"{parts.domain}.{parts.suffix}".lower()


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _encode_host(host: str) -> str:
    host = host.strip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise InvalidUrlError(f"invalid host {host!r}", user_message=BAD_ADDRESS) from error


def _normalise_path(path: str) -> str:
    if not path:
        return "/"
    path = re.sub(r"/{2,}", "/", path)
    trailing = path.endswith("/")
    resolved = posixpath.normpath(unquote(path))
    if resolved in {".", ""}:
        resolved = "/"
    if not resolved.startswith("/"):
        resolved = "/" + resolved
    if trailing and resolved != "/":
        resolved += "/"
    return quote(resolved, safe=_SAFE_PATH)


def _normalise_query(query: str) -> str:
    pairs = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    pairs.sort()
    return urlencode(pairs, quote_via=quote, safe=_SAFE_QUERY)


def normalize_url(raw: str, *, base: str | None = None) -> str:
    """rfc 3986 style normalisation plus tracking-param stripping (plan 03 §5.1.3)."""
    text = unicodedata.normalize("NFKC", raw.strip())
    if not text or len(text) > MAX_INPUT_CHARS or _WHITESPACE_OR_CONTROL.search(text):
        raise InvalidUrlError(f"unusable url {raw!r}", user_message=BAD_ADDRESS)
    if base is not None:
        text = urljoin(base, text)
    elif "://" not in text:
        text = "https://" + text.lstrip("/")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise InvalidUrlError(f"unsupported scheme {scheme!r}", user_message=BAD_ADDRESS)
    if not parts.hostname:
        raise InvalidUrlError(f"missing host in {raw!r}", user_message=BAD_ADDRESS)
    host = _encode_host(parts.hostname)
    try:
        port = parts.port
    except ValueError as error:
        raise InvalidUrlError(f"bad port in {raw!r}", user_message=BAD_ADDRESS) from error
    netloc = host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    return urlunsplit((scheme, netloc, _normalise_path(parts.path), _normalise_query(parts.query), ""))


def dedupe_key(url: str, *, trailing_slash: bool | None = None) -> str:
    """frontier identity: index documents dropped and the trailing slash unified per site."""
    parts = urlsplit(url)
    path = parts.path
    for document in INDEX_DOCUMENTS:
        if path.endswith("/" + document):
            path = path[: -len(document)]
    if path != "/" and trailing_slash is not None:
        path = path.rstrip("/") + ("/" if trailing_slash else "")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def www_twin(host: str) -> str:
    return host[4:] if host.startswith("www.") else f"www.{host}"


def same_registrable_domain(url: str, domain: str) -> bool:
    host = host_of(url)
    return bool(host) and registrable_domain(host) == domain


def in_scope(url: str, scope_hosts: list[str]) -> bool:
    return host_of(url) in scope_hosts
