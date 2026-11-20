import re
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlsplit

import phonenumbers
import regex


class ContactAction(Protocol):
    # an extracted cta candidate or a profile conversion action
    type: str
    label: str
    target: str


# button and field text that says nothing about how a customer gets in touch
GENERIC_LABELS = frozenset(
    {
        "submit",
        "submit now",
        "send",
        "go",
        "ok",
        "next",
        "continue",
        "search",
        "enter",
        "done",
        "save",
        "apply",
        "reset",
        "clear",
        "form",
        "click here",
        "here",
        "get otp",
        "send otp",
        "resend otp",
        "verify",
        "verify otp",
    }
)
_LETTERS = regex.compile(r"\p{L}")
_EMAIL = re.compile(
    r"^[A-Za-z0-9._%+'-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}$"
)
_WHATSAPP_PATH_NUMBER = re.compile(r"^/\+?(\d{6,15})/?$")
PHONE_TYPES = frozenset({"call", "whatsapp"})


def full_phone(raw: str, *regions: str | None) -> str | None:
    """e164 for a complete, valid number; short codes and bare country codes are not phone numbers."""
    number = unquote(raw).strip()
    for region in dict.fromkeys(regions or (None,)):
        try:
            parsed = phonenumbers.parse(number, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return None


def valid_email(value: str) -> bool:
    return bool(_EMAIL.match(value.strip()))


def meaningful_label(text: str | None) -> bool:
    label = " ".join((text or "").split())
    return len(_LETTERS.findall(label)) >= 2 and label.casefold().strip(" .!:>»→") not in GENERIC_LABELS


def meaningful_address(text: str | None) -> bool:
    address = " ".join((text or "").split())
    return len(_LETTERS.findall(address)) >= 4 and len(address.split()) >= 2 and not valid_email(address)


def whatsapp_number(target: str) -> str | None:
    parts = urlsplit(target if "://" in target else f"https://{target}")
    phone = parse_qs(parts.query).get("phone", [""])[0]
    if phone:
        return phone
    match = _WHATSAPP_PATH_NUMBER.match(parts.path) if (parts.hostname or "").endswith("wa.me") else None
    return match.group(1) if match else None


def contact_is_valid(candidate: ContactAction) -> bool:
    """a contact action needs a full phone number, a valid email or a label that names the action;
    an empty link label falls back to the target url, which is fine."""
    target = candidate.target
    if candidate.type == "call" and target.lower().startswith("tel:"):
        return full_phone(target[4:]) is not None
    if candidate.type == "email":
        return target.lower().startswith("mailto:") and valid_email(target[7:])
    if candidate.type == "whatsapp":
        number = whatsapp_number(target)
        return number is None or full_phone(f"+{number.lstrip('+')}") is not None
    return not candidate.label.strip() or meaningful_label(candidate.label)
