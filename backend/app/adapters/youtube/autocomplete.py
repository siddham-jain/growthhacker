import json
import logging

import httpx

logger = logging.getLogger(__name__)

# unofficial endpoint behind youtube's search box; an owner-approved exception like the yt-dlp fallback
SUGGEST_URL = "https://suggestqueries-clients6.youtube.com/complete/search"
TIMEOUT_S = 8.0


def parse_suggestions(body: str) -> list[str]:
    """the endpoint answers jsonp: window.google.ac.h(["seed", [["suggestion", 0, [...]], ...], {...}])"""
    start, end = body.find("("), body.rfind(")")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(body[start + 1 : end])
    except ValueError:
        return []
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return []
    suggestions = []
    for entry in payload[1]:
        text = entry[0] if isinstance(entry, list) and entry else entry
        if isinstance(text, str) and text.strip():
            suggestions.append(text.strip())
    return list(dict.fromkeys(suggestions))


class YouTubeAutocomplete:
    """what youtube suggests while someone types a seed; ordered by youtube, no volumes."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)

    async def suggest(self, seed: str, *, region: str | None, language: str | None) -> list[str]:
        params = {"client": "youtube", "ds": "yt", "q": seed, "hl": language or "en"}
        if region:
            params["gl"] = region
        try:
            response = await self._client.get(SUGGEST_URL, params=params)
        except httpx.HTTPError as error:
            logger.warning("youtube autocomplete failed", extra={"seed": seed, "error": type(error).__name__})
            return []
        if response.status_code != 200:
            logger.warning(
                "youtube autocomplete http error", extra={"seed": seed, "status": response.status_code}
            )
            return []
        return parse_suggestions(response.text)
