from dataclasses import replace

import pytest

from app.services.recommend.context import CompetitorInfo
from app.services.recommend.creators.collabs import CollaborationDetector, collaboration_label
from tests.recommend.fixtures import CHANNEL_BY_KEY, SPECS, channel_id, channel_record, video_record

FORTIS = CompetitorInfo(
    channel_id=channel_id("fortis"),
    name="Fortis Healthcare",
    handle="@FortisHealthcare",
    aliases=("Fortis", "Fortis Healthcare"),
    domain="fortishealthcare.com",
)
DETECTOR = CollaborationDetector(["sponsored by", "#ad", "प्रायोजित"], ["knee replacement", "angioplasty"])
CREATOR = channel_record(CHANNEL_BY_KEY["drheart"])


def upload(description: str, title: str = "Weekly heart talk", paid: bool | None = False):
    return video_record(replace(SPECS["a2"], description=description, title=title, paid=paid))


@pytest.mark.parametrize(
    ("description", "paid", "expected"),
    [
        ("Sponsored by @FortisHealthcare", False, ("sponsor_language", "handle")),
        ("Thanks to @FortisHealthcare for the tour", False, ("mentions_competitor", "handle")),
        ("Visit fortishealthcare.com for details", True, ("disclosed_paid_promotion", "website_domain")),
        ("Watch youtube.com/@FortisHealthcare too", False, ("mentions_competitor", "custom_url")),
        ("Fortis team explains angioplasty #ad", False, ("sponsor_language", "brand_name")),
        ("प्रायोजित: @FortisHealthcare", False, ("sponsor_language", "handle")),
    ],
)
def test_creator_uploads_mentioning_a_competitor(
    description: str, paid: bool, expected: tuple[str, str]
) -> None:
    (found,) = DETECTOR.detect(CREATOR, [upload(description, paid=paid)], [FORTIS], [])

    assert (found.kind, found.matched_on) == expected


def test_ambiguous_brand_names_need_a_niche_keyword_in_the_same_text() -> None:
    tyres = upload("Fortis tyres review", title="Road trip vlog")

    assert DETECTOR.detect(CREATOR, [tyres], [FORTIS], []) == []


def test_a_competitor_upload_naming_the_creators_handle_is_a_mention_by_the_competitor() -> None:
    competitor_upload = video_record(replace(SPECS["k1"], description="With our guest @DrHeartTalks"))
    (found,) = DETECTOR.detect(CREATOR, [], [FORTIS], [competitor_upload])

    assert found.kind == "mentioned_by_competitor"
    assert collaboration_label(found).startswith("Mentioned by Fortis Healthcare on ")


def test_display_names_alone_never_label_a_collaboration() -> None:
    competitor_upload = video_record(replace(SPECS["k1"], description="Dr Heart Talks was great"))

    assert DETECTOR.detect(CREATOR, [], [FORTIS], [competitor_upload]) == []


def test_labels_never_claim_a_sponsor() -> None:
    (found,) = DETECTOR.detect(CREATOR, [upload("fortishealthcare.com", paid=True)], [FORTIS], [])

    label = collaboration_label(found)
    assert "sponsored by" not in label.lower()
    assert label.startswith("Disclosed paid promotion on ")
