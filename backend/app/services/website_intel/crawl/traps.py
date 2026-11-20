import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlsplit

CALENDAR_PATH = re.compile(r"/(?:19|20)\d{2}/(?:0?[1-9]|1[0-2])(?:/\d{1,2})?(?=/|$)")
PAGINATION_PATH = re.compile(r"/page/(\d+)(?=/|$)")


@dataclass(frozen=True)
class TrapRules:
    max_query_params: int = 3
    session_params: frozenset[str] = frozenset({"sid", "sessionid", "phpsessid", "jsessionid"})
    facet_params: frozenset[str] = frozenset({"sort", "order", "filter", "facet"})
    calendar_params: frozenset[str] = frozenset({"date", "month", "year", "day", "cal"})
    commerce_params: frozenset[str] = frozenset({"replytocom", "add-to-cart"})
    search_params: frozenset[str] = frozenset({"s", "q", "query", "search"})
    pagination_params: frozenset[str] = frozenset({"page", "p", "start"})
    max_pagination: int = 3
    max_calendar_values: int = 3
    max_repeated_segment: int = 2
    max_depth: int = 8
    max_url_length: int = 300
    excluded_segments: frozenset[str] = frozenset(
        {
            "login",
            "log-in",
            "signin",
            "sign-in",
            "logout",
            "signup",
            "sign-up",
            "register",
            "cart",
            "checkout",
            "basket",
            "account",
            "my-account",
            "myaccount",
            "admin",
            "wp-admin",
            "wp-login.php",
            "search",
        }
    )
    excluded_extensions: frozenset[str] = frozenset(
        {
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tif", ".tiff", ".avif",
            ".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v", ".mp3", ".wav", ".ogg", ".m4a", ".flac",
            ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf", ".csv",
            ".ics", ".xml", ".json", ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".exe", ".dmg", ".apk",
        }
    )  # fmt: skip
    quarantine_threshold: int = 2000


@dataclass
class TrapDetector:
    """stateful per crawl: calendar and parameter-pattern caps accumulate across calls."""

    rules: TrapRules = field(default_factory=TrapRules)
    _calendar_values: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _param_patterns: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _quarantined: set[str] = field(default_factory=set)

    def reason(self, url: str) -> str | None:
        rules = self.rules
        if len(url) > rules.max_url_length:
            return "too_long"
        parts = urlsplit(url)
        path = unquote(parts.path or "/")
        segments = [segment for segment in path.lower().split("/") if segment]
        query_reason = self._query_reason(parts.query)
        if query_reason:
            return query_reason
        if len(segments) > rules.max_depth:
            return "too_deep"
        if segments and Counter(segments).most_common(1)[0][1] > rules.max_repeated_segment:
            return "repeated_segment"
        excluded = next((segment for segment in segments if segment in rules.excluded_segments), None)
        if excluded:
            return f"excluded_page:{excluded}"
        if segments:
            leaf = segments[-1]
            extension = leaf[leaf.rfind(".") :] if "." in leaf else ""
            if extension in rules.excluded_extensions:
                return f"non_html:{extension}"
        pagination = PAGINATION_PATH.search(path)
        if pagination and int(pagination.group(1)) > rules.max_pagination:
            return "pagination"
        calendar = CALENDAR_PATH.search(path)
        if calendar:
            template = path[: calendar.start()] + "/{date}" + path[calendar.end() :]
            values = self._calendar_values[template]
            values.add(calendar.group(0))
            if len(values) > rules.max_calendar_values:
                return "calendar"
        if parts.query:
            pattern = parts.path + "?" + ",".join(sorted({key for key, _ in parse_qsl(parts.query)}))
            if pattern in self._quarantined:
                return "quarantined_params"
            seen = self._param_patterns[pattern]
            seen.add(url)
            if len(seen) > rules.quarantine_threshold:
                self._quarantined.add(pattern)
                return "quarantined_params"
        return None

    def _query_reason(self, query: str) -> str | None:
        if not query:
            return None
        rules = self.rules
        pairs = parse_qsl(query, keep_blank_values=True)
        if len(pairs) > rules.max_query_params:
            return "too_many_params"
        for key, value in pairs:
            name = key.lower()
            if name in rules.session_params:
                return f"trap_param:{name}"
            if name in rules.facet_params or name in rules.calendar_params or name in rules.commerce_params:
                return f"trap_param:{name}"
            if name in rules.search_params:
                return "excluded_page:search"
            if name in rules.pagination_params and (not value.isdigit() or int(value) > rules.max_pagination):
                return "pagination"
        return None
