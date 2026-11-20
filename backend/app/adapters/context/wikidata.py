from typing import Any
from urllib.parse import quote

import httpx

from app.services.website_intel.identity.knowledge import KbEntity, KbRelated, KbSummary

PROPERTY_WEBSITE = "P856"
PROPERTY_INDUSTRY = "P452"
PROPERTY_HEADQUARTERS = "P159"
PROPERTY_COUNTRY = "P17"
PROPERTY_SUBSIDIARY = "P355"
PROPERTY_PARENT = "P749"
PROPERTY_PRODUCT = "P1056"
PROPERTY_INCEPTION = "P571"
LINKED_PROPERTIES = (
    PROPERTY_INDUSTRY,
    PROPERTY_HEADQUARTERS,
    PROPERTY_COUNTRY,
    PROPERTY_SUBSIDIARY,
    PROPERTY_PARENT,
    PROPERTY_PRODUCT,
)
MAX_LINKED_IDS = 50


def _values(claims: dict[str, Any], prop: str) -> list[Any]:
    return [
        claim["mainsnak"]["datavalue"]["value"]
        for claim in claims.get(prop, [])
        if claim.get("mainsnak", {}).get("datavalue") and claim.get("rank") != "deprecated"
    ]


def _item_ids(claims: dict[str, Any], prop: str) -> list[str]:
    return [value["id"] for value in _values(claims, prop) if isinstance(value, dict) and "id" in value]


def _label(entity: dict[str, Any], language: str) -> str | None:
    labels = entity.get("labels", {})
    chosen = labels.get(language) or labels.get("en") or next(iter(labels.values()), None)
    return chosen["value"] if chosen else None


class WikidataKnowledgeBase:
    """wikidata entity search plus the wikipedia rest summary; free, cheap, no origin contact."""

    def __init__(self, client: httpx.AsyncClient, *, api_url: str, summary_url: str, user_agent: str) -> None:
        self._client = client
        self._api_url = api_url
        self._summary_url = summary_url
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    async def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
        try:
            response = await self._client.get(
                url, params=params, headers=self._headers, timeout=20, follow_redirects=True
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def search(self, name: str, *, language: str = "en", limit: int = 5) -> list[KbEntity]:
        found = await self._get(
            self._api_url,
            {
                "action": "wbsearchentities",
                "search": name,
                "language": language,
                "uselang": language,
                "type": "item",
                "limit": str(limit),
                "format": "json",
            },
        )
        ids = [item["id"] for item in (found or {}).get("search", []) if "id" in item]
        if not ids:
            return []
        entities = await self._entities(ids, language, props="labels|descriptions|aliases|claims|sitelinks")
        linked = sorted(
            {
                qid
                for entity in entities.values()
                for prop in LINKED_PROPERTIES
                for qid in _item_ids(entity.get("claims", {}), prop)
            }
        )[:MAX_LINKED_IDS]
        linked_entities = await self._entities(linked, language, props="labels|claims") if linked else {}
        return [self._to_entity(entities[qid], linked_entities, language) for qid in ids if qid in entities]

    async def _entities(self, ids: list[str], language: str, *, props: str) -> dict[str, dict[str, Any]]:
        payload = await self._get(
            self._api_url,
            {
                "action": "wbgetentities",
                "ids": "|".join(ids),
                "props": props,
                "languages": f"{language}|en",
                "format": "json",
            },
        )
        return {
            qid: entity
            for qid, entity in (payload or {}).get("entities", {}).items()
            if "missing" not in entity
        }

    def _to_entity(
        self, entity: dict[str, Any], linked: dict[str, dict[str, Any]], language: str
    ) -> KbEntity:
        claims = entity.get("claims", {})

        def labels(prop: str) -> list[str]:
            return [
                label for qid in _item_ids(claims, prop) if (label := _label(linked.get(qid, {}), language))
            ]

        def related(prop: str) -> list[KbRelated]:
            items = []
            for qid in _item_ids(claims, prop):
                target = linked.get(qid, {})
                label = _label(target, language)
                if label:
                    websites = _values(target.get("claims", {}), PROPERTY_WEBSITE)
                    items.append(KbRelated(name=label, website=websites[0] if websites else None))
            return items

        inception = next(
            (value.get("time") for value in _values(claims, PROPERTY_INCEPTION) if isinstance(value, dict)),
            None,
        )
        sitelink = entity.get("sitelinks", {}).get(f"{language}wiki") or entity.get("sitelinks", {}).get(
            "enwiki"
        )
        aliases = entity.get("aliases", {}).get(language) or entity.get("aliases", {}).get("en") or []
        description = entity.get("descriptions", {}).get(language) or entity.get("descriptions", {}).get("en")
        return KbEntity(
            qid=entity["id"],
            label=_label(entity, language) or entity["id"],
            description=description["value"] if description else None,
            aliases=[alias["value"] for alias in aliases][:10],
            websites=[value for value in _values(claims, PROPERTY_WEBSITE) if isinstance(value, str)],
            industries=labels(PROPERTY_INDUSTRY),
            headquarters=labels(PROPERTY_HEADQUARTERS),
            countries=labels(PROPERTY_COUNTRY),
            subsidiaries=related(PROPERTY_SUBSIDIARY),
            parents=related(PROPERTY_PARENT),
            products=labels(PROPERTY_PRODUCT),
            inception_year=int(inception[1:5])
            if isinstance(inception, str) and inception[1:5].isdigit()
            else None,
            wikipedia_title=sitelink["title"] if sitelink else None,
            wikipedia_lang=language if entity.get("sitelinks", {}).get(f"{language}wiki") else "en",
        )

    async def summary(self, title: str, *, language: str = "en") -> KbSummary | None:
        url = self._summary_url.format(lang=language, title=quote(title.replace(" ", "_"), safe=""))
        payload = await self._get(url)
        if not payload or not payload.get("extract"):
            return None
        page_url = payload.get("content_urls", {}).get("desktop", {}).get("page") or url
        return KbSummary(title=payload.get("title") or title, extract=payload["extract"], url=page_url)
