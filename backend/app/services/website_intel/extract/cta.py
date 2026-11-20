import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import phonenumbers

from app.services.website_intel.extract.contact import contact_is_valid, full_phone, meaningful_label
from app.services.website_intel.models import CtaCandidate, CtaMedium, CtaType, LinkRegion, PageLink

SCHEDULING_HOSTS = (
    "calendly.com",
    "cal.com",
    "meetings.hubspot.com",
    "zcal.co",
    "savvycal.com",
    "practo.com",
    "acuityscheduling.com",
    "setmore.com",
)
APP_STORE_PREFIXES = ("apps.apple.com/", "itunes.apple.com/", "play.google.com/store/apps")
WHATSAPP_PREFIXES = ("wa.me/", "api.whatsapp.com/send", "web.whatsapp.com/send", "whatsapp.com/send")
TLD_REGION_OVERRIDES = {"uk": "GB"}
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_AUTOLINK = re.compile(r"<((?:https?://|mailto:|tel:)[^>\s]+)>")
_DIGITS = re.compile(r"[^\d+]")

# anchor lexicon in en, hi and hi-Latn; first match wins
ANCHOR_LEXICON: tuple[tuple[re.Pattern[str], CtaType], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), cta_type)
    for pattern, cta_type in (
        (r"\b(?:request|book|schedule|get|watch)\s+(?:a\s+|your\s+)?(?:free\s+)?demo\b", "demo"),
        (
            r"\b(?:book|schedule|request|make)\s+(?:an?\s+|your\s+)?(?:doctor\s+)?appointment\b",
            "book_appointment",
        ),
        (
            r"\b(?:book|schedule)\s+(?:a\s+|your\s+)?(?:visit|consultation|consult|slot|session|test|now)\b",
            "book_appointment",
        ),
        (r"\bappointment\s+book\s+kar", "book_appointment"),
        (r"अपॉइंटमेंट\s*बुक", "book_appointment"),
        (
            r"\bstart\s+(?:your\s+|a\s+)?(?:free\s+)?trial\b|\bfree\s+trial\b|\btry\s+(?:it\s+)?(?:for\s+)?free\b",
            "free_trial",
        ),
        (r"\b(?:request|get)\s+(?:a\s+)?(?:quote|estimate|proposal)\b", "quote_request"),
        (r"\b(?:get\s+started|sign\s*up|create\s+(?:an?\s+)?account|register\s+now|join\s+now)\b", "signup"),
        (r"\b(?:buy|shop|order)\s+now\b|\badd\s+to\s+(?:cart|bag|basket)\b", "buy"),
        (r"\bchat\s+(?:with\s+us\s+)?on\s+whatsapp\b|\bwhatsapp\s+us\b", "whatsapp"),
        (r"\bcall\s+(?:us\s+)?now\b|\bcall\s+us\b|\babhi\s+call\s+kar", "call"),
        (r"अभी\s*कॉल\s*करें|कॉल\s*करें", "call"),
        (
            r"\b(?:request\s+(?:a\s+)?call\s*back|contact\s+us|get\s+in\s+touch|enquire\s+now|enquiry|send\s+(?:an?\s+)?enquiry)\b",
            "contact_form",
        ),
        (r"\bsampark\s+kar|संपर्क\s*करें", "contact_form"),
        (r"\bdownload\s+(?:the\s+|our\s+)?(?:app|brochure|guide|ebook|whitepaper|report)\b", "download"),
        (r"\b(?:subscribe|newsletter)\b", "newsletter"),
        (
            r"\b(?:get\s+directions|visit\s+us|find\s+(?:a|your|the\s+nearest)\s+(?:hospital|clinic|store|branch|location|centre|center))\b",
            "visit_location",
        ),
    )
)
# strong booking/demo paths when the anchor itself says nothing usable
PATH_LEXICON: tuple[tuple[re.Pattern[str], CtaType], ...] = (
    (
        re.compile(
            r"/(?:book[-_]?(?:an?[-_])?(?:doctor[-_])?appointment|appointments?/book|schedule[-_]appointment)"
        ),
        "book_appointment",
    ),
    (re.compile(r"/(?:request|book|schedule)[-_](?:a[-_])?demo\b"), "demo"),
    (re.compile(r"/(?:signup|sign-up|register)/?$"), "signup"),
    (re.compile(r"/free[-_]trial\b"), "free_trial"),
)
MEDIUM_BY_TYPE: dict[CtaType, CtaMedium] = {
    "call": "phone",
    "whatsapp": "whatsapp",
    "email": "email",
    "app_install": "app",
    "visit_location": "in_person",
}
BOOKING_FIELDS = ("date", "time", "slot", "appointment", "doctor", "speciality", "specialty")
CONTACT_FIELDS = ("phone", "mobile", "tel", "name", "message", "enquiry", "query")


@dataclass(frozen=True)
class FormInfo:
    action: str
    fields: tuple[str, ...]
    submit_label: str = ""


def markdown_links(markdown: str) -> list[PageLink]:
    text = _MARKDOWN_IMAGE.sub(lambda match: match.group(1), markdown)
    links = [
        PageLink(url=match.group(2).strip(), anchor=" ".join(match.group(1).split()))
        for match in _MARKDOWN_LINK.finditer(text)
    ]
    links.extend(PageLink(url=match.group(1)) for match in _AUTOLINK.finditer(text))
    return links


def region_for_country(country_hint: str | None, page_url: str) -> str | None:
    if country_hint:
        return country_hint.upper()
    tld = (urlsplit(page_url).hostname or "").rsplit(".", 1)[-1].lower()
    if len(tld) == 2 and tld.isalpha():
        return TLD_REGION_OVERRIDES.get(tld, tld.upper())
    return None


def normalize_phone(raw: str, *regions: str | None) -> str:
    number = unquote(raw).strip()
    return full_phone(number, *regions) or _DIGITS.sub("", number)


def international(number: str) -> str:
    return phonenumbers.format_number(
        phonenumbers.parse(number), phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )


def _without_scheme(url: str) -> str:
    return url.split("://", 1)[-1].lower().removeprefix("www.")


class CtaExtractor:
    """deterministic conversion actions from links and forms (plan 03 §5.7.6)."""

    def extract(
        self,
        links: Iterable[PageLink],
        *,
        page_url: str,
        forms: Sequence[FormInfo] = (),
        country_hint: str | None = None,
    ) -> list[CtaCandidate]:
        region = region_for_country(country_hint, page_url)
        site_region = region_for_country(None, page_url)
        found: dict[str, CtaCandidate] = {}
        for link in links:
            candidate = self._from_link(link, page_url=page_url, regions=(region, site_region))
            if candidate is not None and contact_is_valid(candidate):
                self._merge(found, candidate)
        for form in forms:
            candidate = self._from_form(form, page_url=page_url)
            if candidate is not None and contact_is_valid(candidate):
                self._merge(found, candidate)
        return list(found.values())

    def _merge(self, found: dict[str, CtaCandidate], candidate: CtaCandidate) -> None:
        current = found.get(candidate.key)
        if current is None:
            found[candidate.key] = candidate
            return
        found[candidate.key] = current.model_copy(
            update={
                "label": current.label or candidate.label,
                "in_header": current.in_header or candidate.in_header,
                "in_footer": current.in_footer or candidate.in_footer,
            }
        )

    def _from_link(
        self, link: PageLink, *, page_url: str, regions: tuple[str | None, ...]
    ) -> CtaCandidate | None:
        target = link.url.strip()
        lowered = target.lower()
        label = link.anchor
        cta_type: CtaType | None = None
        if lowered.startswith("tel:"):
            cta_type, target = "call", "tel:" + normalize_phone(target[4:], *regions)
            number = full_phone(target[4:])
            # a bare "+91" or icon anchor on a valid tel link reads as the number itself
            if number and not (meaningful_label(label) or full_phone(label, *regions)):
                label = international(number)
        elif lowered.startswith("mailto:"):
            cta_type, target = "email", "mailto:" + target[7:].split("?", 1)[0].strip().lower()
        elif lowered.startswith("whatsapp:") or _without_scheme(target).startswith(WHATSAPP_PREFIXES):
            cta_type = "whatsapp"
        elif not lowered.startswith(("http://", "https://")):
            return None
        elif _without_scheme(target).startswith(APP_STORE_PREFIXES):
            cta_type = "app_install"
        else:
            cta_type = self._web_type(link, target)
        if cta_type is None:
            return None
        if lowered.startswith(("tel:", "mailto:")) and len(target.split(":", 1)[1]) < 3:
            return None
        if cta_type == "email" and not meaningful_label(label):
            label = target[7:]
        return CtaCandidate(
            type=cta_type,
            label=label,
            target=target,
            medium=self._medium(cta_type, target),
            page_url=page_url,
            in_header=link.region in {LinkRegion.header, LinkRegion.nav},
            in_footer=link.region is LinkRegion.footer,
        )

    def _web_type(self, link: PageLink, target: str) -> CtaType | None:
        host = (urlsplit(target).hostname or "").lower().removeprefix("www.")
        anchor_type = next((kind for pattern, kind in ANCHOR_LEXICON if pattern.search(link.anchor)), None)
        if any(host == name or host.endswith("." + name) for name in SCHEDULING_HOSTS):
            return anchor_type if anchor_type == "demo" else "book_appointment"
        if anchor_type is not None:
            return anchor_type
        path = urlsplit(target).path.lower()
        return next((kind for pattern, kind in PATH_LEXICON if pattern.search(path)), None)

    def _medium(self, cta_type: CtaType, target: str) -> CtaMedium:
        if target.startswith(("http://", "https://")) and cta_type in {"call", "visit_location"}:
            return "web"
        return MEDIUM_BY_TYPE.get(cta_type, "web")

    def _from_form(self, form: FormInfo, *, page_url: str) -> CtaCandidate | None:
        fields = " ".join(form.fields)
        if "password" in fields or "search" in fields:
            return None
        # "Submit", "Get OTP" or a "+91" dial-code button says nothing about what the form is for
        if not any(pattern.search(form.submit_label) for pattern, _ in ANCHOR_LEXICON):
            return None
        if any(name in fields for name in BOOKING_FIELDS):
            cta_type: CtaType = "book_appointment"
        elif any(name in fields for name in CONTACT_FIELDS):
            cta_type = "contact_form"
        elif "email" in fields:
            cta_type = "newsletter"
        else:
            return None
        return CtaCandidate(
            type=cta_type,
            label=form.submit_label or "form",
            target=form.action,
            medium="web",
            page_url=page_url,
        )
