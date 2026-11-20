from typing import Literal

from app.domain.analytics import VideoTraits

PatternKind = Literal["title", "thumbnail", "format"]

TITLE_FLAGS = (
    "is_question",
    "has_number",
    "has_listicle_number",
    "is_how_to",
    "has_year",
    "has_brackets",
    "has_parentheses",
    "is_first_person",
    "has_superlative",
    "has_emoji",
    "has_price_cost",
    "has_buyer_question_phrasing",
    "mentions_doctor",
    "has_client_location",
    "has_niche_location",
)
TITLE_BANDS = ("char_len_band", "caps_band", "separator", "script")
THUMBNAIL_FLAGS = ("text_present",)
THUMBNAIL_BANDS = ("word_count_band", "contrast_band", "brightness_band", "colorfulness_band")
FORMAT_KEYS = ("format_key", "segment", "length_band")
LOCATION_TRAITS = ("title.has_client_location", "title.has_niche_location")

TITLE_FLAG_LABELS: dict[str, tuple[str, str]] = {
    "is_question": ("question titles", "titles that are not questions"),
    "has_number": ("a number in the title", "no number in the title"),
    "has_listicle_number": ("list titles", "titles that are not lists"),
    "is_how_to": ("how-to titles", "titles that are not how-tos"),
    "has_year": ("the year in the title", "no year in the title"),
    "has_brackets": ("brackets in the title", "no brackets in the title"),
    "has_parentheses": ("parentheses in the title", "no parentheses in the title"),
    "is_first_person": ("first-person titles", "titles not in the first person"),
    "has_superlative": ("a superlative in the title", "no superlative in the title"),
    "has_emoji": ("an emoji in the title", "no emoji in the title"),
    "has_price_cost": ("a price or cost in the title", "no price or cost in the title"),
    "has_buyer_question_phrasing": ("buyer-question phrasing", "no buyer-question phrasing"),
    "mentions_doctor": ("a doctor named in the title", "no doctor named in the title"),
    "has_client_location": ("a location in the title", "no location in the title"),
    "has_niche_location": ("a location in the title", "no location in the title"),
}
SLOT_LABELS: dict[str, str] = {
    "is_question": "[Question]",
    "has_number": "[Number]",
    "has_listicle_number": "[List number]",
    "is_how_to": "[How to]",
    "has_year": "[Year]",
    "has_brackets": "[Bracketed detail]",
    "has_parentheses": "(Detail)",
    "is_first_person": "[I / We]",
    "has_superlative": "[Best / Most]",
    "has_price_cost": "[Price]",
    "has_buyer_question_phrasing": "[Buyer question]",
    "mentions_doctor": "[Doctor]",
    "has_client_location": "[City]",
    "has_niche_location": "[City]",
}
SEGMENT_LABELS = {"short_form": "Shorts", "long_form": "long-form videos", "live": "live streams"}


def flatten_traits(traits: VideoTraits) -> dict[str, str]:
    # trait keys and values exactly as 06 mines them, e.g. "title.is_question" -> "true"
    title = traits.title
    flat = {f"title.{name}": _bool(getattr(title, name)) for name in TITLE_FLAGS}
    for name in TITLE_BANDS:
        value = getattr(title, name)
        if value is not None:
            flat[f"title.{name}"] = str(value)
    thumbnail = traits.thumbnail
    if thumbnail is not None:
        flat.update({f"thumbnail.{name}": _bool(getattr(thumbnail, name)) for name in THUMBNAIL_FLAGS})
        for name in THUMBNAIL_BANDS:
            value = getattr(thumbnail, name)
            if value is not None:
                flat[f"thumbnail.{name}"] = str(value)
    flat["segment"] = traits.segment.value
    flat["length_band"] = traits.length_band
    flat["format_key"] = traits.format_key
    return flat


def _bool(value: bool) -> str:
    return "true" if value else "false"


def trait_kind(trait_key: str) -> PatternKind | None:
    if trait_key.startswith("title."):
        return "title"
    if trait_key.startswith("thumbnail."):
        return "thumbnail"
    if trait_key in FORMAT_KEYS:
        return "format"
    return None


def format_label(format_key: str) -> str:
    segment, _, band = format_key.partition("|")
    return f"{band} {SEGMENT_LABELS.get(segment, segment)}".strip()


def trait_label(trait_key: str, trait_value: str) -> str:
    name = trait_key.partition(".")[2]
    if trait_key.startswith("title.") and name in TITLE_FLAG_LABELS:
        positive, negative = TITLE_FLAG_LABELS[name]
        return positive if trait_value == "true" else negative
    if trait_key == "title.char_len_band":
        return f"titles of {trait_value} characters"
    if trait_key == "title.separator":
        return f"a {trait_value} separator in the title"
    if trait_key == "title.caps_band":
        return f"capital letters at {trait_value} of the title"
    if trait_key == "title.script":
        return f"{trait_value}-script titles"
    if trait_key == "thumbnail.text_present":
        return "text on the thumbnail" if trait_value == "true" else "no text on the thumbnail"
    if trait_key == "thumbnail.word_count_band":
        return f"{trait_value} words on the thumbnail"
    if trait_key.startswith("thumbnail.") and trait_key.endswith("_band"):
        quality = name.removesuffix("_band")
        return f"{trait_value} {quality} thumbnails"
    if trait_key == "segment":
        return SEGMENT_LABELS.get(trait_value, trait_value)
    if trait_key == "length_band":
        return f"{trait_value} videos"
    if trait_key == "format_key":
        return format_label(trait_value)
    return f"{trait_key} = {trait_value}"


def traits_label(traits: dict[str, str]) -> str:
    return " + ".join(trait_label(key, value) for key, value in sorted(traits.items()))


def slot_template(traits: dict[str, str]) -> str:
    # a code-built pattern such as "[Question] … [Number] … [Year]"; never an example title
    slots = [
        SLOT_LABELS[key.partition(".")[2]]
        for key, value in sorted(traits.items())
        if key.startswith("title.") and value == "true" and key.partition(".")[2] in SLOT_LABELS
    ]
    if not slots:
        return traits_label(traits)
    return " … ".join(dict.fromkeys(slots))
