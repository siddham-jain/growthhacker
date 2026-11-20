from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import regex

URL = regex.compile(r"https?://[^\s<>\"'`]+", regex.IGNORECASE)
NESTED_SCHEME = regex.compile(r"https?:/{1,2}", regex.IGNORECASE)
HOST_LABEL = regex.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
TRAILING = ".,;:!?'\"”’)]}"


@dataclass(frozen=True, slots=True)
class ExtractedUrl:
    raw: str
    start: int
    end: int
    line_index: int
    normalised: str
    host: str


@dataclass(frozen=True, slots=True)
class Malformation:
    reason: str
    repaired: str | None


def _trim(candidate: str) -> str:
    while candidate and candidate[-1] in TRAILING:
        if candidate[-1] == ")" and candidate.count("(") >= candidate.count(")"):
            break
        candidate = candidate[:-1]
    return candidate


def host_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def normalise_url(url: str) -> str:
    # scheme, www, utm parameters and trailing slashes never make two links different
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)]
    kept = [(key, value) for key, value in query if not key.lower().startswith("utm_")]
    suffix = f"?{urlencode(kept)}" if kept else ""
    return f"{host}{path}{suffix}"


def same_target(link: str, target: str) -> bool:
    link_norm, target_norm = normalise_url(link), normalise_url(target)
    return link_norm == target_norm or link_norm.startswith(target_norm.split("?")[0] + "/")


def on_domains(url: str, domains: frozenset[str]) -> bool:
    host = host_of(url)
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def extract_urls(text: str, newline: str = "\n") -> list[ExtractedUrl]:
    found: list[ExtractedUrl] = []
    for match in URL.finditer(text):
        raw = _trim(match.group(0))
        if not raw:
            continue
        start = match.start()
        line_index = text.count(newline, 0, start)
        found.append(
            ExtractedUrl(
                raw=raw,
                start=start,
                end=start + len(raw),
                line_index=line_index,
                normalised=normalise_url(raw),
                host=host_of(raw),
            )
        )
    return found


def _valid_host(host: str) -> bool:
    if not host or "." not in host or len(host) > 253:
        return False
    labels = host.split(".")
    return all(HOST_LABEL.match(label) for label in labels) and not labels[-1].isdigit()


def malformation(url: str) -> Malformation | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return Malformation("unparseable", None)
    host = (parts.hostname or "").lower()
    if not _valid_host(host):
        return Malformation("invalid host", None)
    tail = url[len(f"{parts.scheme}://") :]
    nested = list(NESTED_SCHEME.finditer(tail))
    if nested:
        repaired = "https://" + tail[nested[-1].end() :]
        # repair only when the embedded address points at the same site, so the fix is unambiguous
        if host_of(repaired) == host.removeprefix("www.") and _valid_host(host_of(repaired)):
            return Malformation("contains the address twice", repaired)
        return Malformation("contains another address inside it", None)
    bare_host = host.removeprefix("www.")
    path = parts.path.lstrip("/")
    for prefix in (f"www.{bare_host}/", f"{bare_host}/"):
        if path.lower().startswith(prefix):
            repaired = urlunsplit((parts.scheme, parts.netloc, "/" + path[len(prefix) :], parts.query, ""))
            return Malformation("repeats the domain in its path", repaired)
    return None


def add_utm(url: str, campaign: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key.lower().startswith("utm_") for key, _ in query):
        return url
    query += [("utm_source", "youtube"), ("utm_medium", "video_description"), ("utm_campaign", campaign)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
