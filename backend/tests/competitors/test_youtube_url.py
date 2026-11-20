import pytest

from app.services.competitors.resolve.youtube_url import (
    NameRef,
    RejectedRef,
    WebsiteRef,
    YouTubeRef,
    parse_youtube_url,
)

CID = "UCn0ShMNqXI3kQP-Eoaf7olg"
VID = "Mcek5LoOMZA"

YOUTUBE_CASES = [
    (CID, "channel_id", CID),
    (f"https://www.youtube.com/channel/{CID}", "channel_id", CID),
    (f"https://youtube.com/channel/{CID}/videos", "channel_id", CID),
    (f"http://m.youtube.com/channel/{CID}?si=abc", "channel_id", CID),
    (f"youtube.com/channel/{CID}", "channel_id", CID),
    ("@fortishealthcareofficial", "handle", "fortishealthcareofficial"),
    ("https://www.youtube.com/@fortishealthcareofficial", "handle", "fortishealthcareofficial"),
    ("https://www.youtube.com/@fortishealthcareofficial/videos", "handle", "fortishealthcareofficial"),
    ("https://www.youtube.com/@%E0%A4%A1%E0%A5%89%E0%A4%95%E0%A5%8D%E0%A4%9F%E0%A4%B0", "handle", "डॉक्टर"),
    ("@डॉक्टरसाहब", "handle", "डॉक्टरसाहब"),
    ("https://www.youtube.com/@medanta?feature=shared", "handle", "medanta"),
    ("https://www.youtube.com/user/MedantaTheMedicity", "username", "MedantaTheMedicity"),
    ("https://youtube.com/user/maxhcvideos/featured", "username", "maxhcvideos"),
    ("https://www.youtube.com/c/FortisHealthcare", "custom", "FortisHealthcare"),
    ("https://www.youtube.com/fortishealthcare", "custom", "fortishealthcare"),
    ("www.youtube.com/fortishealthcare/videos", "custom", "fortishealthcare"),
    (f"https://www.youtube.com/watch?v={VID}", "video", VID),
    (f"https://www.youtube.com/watch?v={VID}&t=42s&list=PL1234567890ab", "video", VID),
    (f"https://m.youtube.com/watch?v={VID}&utm_source=x", "video", VID),
    (f"https://youtu.be/{VID}", "video", VID),
    (f"https://youtu.be/{VID}?si=Xyz123", "video", VID),
    (f"youtu.be/{VID}", "video", VID),
    (f"https://www.youtube.com/shorts/{VID}", "video", VID),
    (f"https://youtube.com/shorts/{VID}?feature=share", "video", VID),
    (f"https://www.youtube.com/live/{VID}", "video", VID),
    (f"https://www.youtube.com/embed/{VID}", "video", VID),
    (f"https://www.youtube.com/embed/{VID}?rel=0", "video", VID),
    (f"https://www.youtube-nocookie.com/embed/{VID}", "video", VID),
    (f"https://www.youtube.com/v/{VID}", "video", VID),
    ("https://www.youtube.com/playlist?list=PLabcdefghijklmnop", "playlist", "PLabcdefghijklmnop"),
    (
        "https://www.youtube.com/playlist?list=UUn0ShMNqXI3kQP-Eoaf7olg",
        "playlist",
        "UUn0ShMNqXI3kQP-Eoaf7olg",
    ),
    ("https://www.youtube.com/watch?list=PLabcdefghijklmnop", "playlist", "PLabcdefghijklmnop"),
    (f"https://www.youtube.com/attribution_link?a=x&u=/watch%3Fv%3D{VID}%26feature%3Dshare", "video", VID),
    (f"  https://www.youtube.com/watch?v={VID}  ", "video", VID),
    (f"HTTPS://WWW.YOUTUBE.COM/channel/{CID}", "channel_id", CID),
]


@pytest.mark.parametrize(("raw", "kind", "value"), YOUTUBE_CASES)
def test_youtube_forms(raw, kind, value):
    assert parse_youtube_url(raw) == YouTubeRef(kind=kind, value=value)


@pytest.mark.parametrize(
    "raw",
    [
        "https://music.youtube.com/watch?v=Mcek5LoOMZA",
        "https://www.instagram.com/apollohospitals/",
        "https://www.tiktok.com/@apollo",
        "https://x.com/HospitalsApollo",
        "https://twitter.com/HospitalsApollo",
        "https://www.linkedin.com/company/apollo-hospitals",
        "https://www.youtube.com/",
        "https://www.youtube.com/results?search_query=apollo",
        "https://www.youtube.com/watch?v=short",
        "@a",
    ],
)
def test_rejected_inputs(raw):
    assert isinstance(parse_youtube_url(raw), RejectedRef)


def test_other_platforms_have_a_clear_reason():
    assert parse_youtube_url("https://instagram.com/x").reason == "other platforms are out of scope"


@pytest.mark.parametrize(
    ("raw", "domain"),
    [
        ("https://www.fortishealthcare.com/", "fortishealthcare.com"),
        ("medanta.org", "medanta.org"),
        ("https://www.apollohospitals.co.in/delhi", "apollohospitals.co.in"),
    ],
)
def test_websites(raw, domain):
    parsed = parse_youtube_url(raw)
    assert isinstance(parsed, WebsiteRef) and parsed.domain == domain


@pytest.mark.parametrize("raw", ["Fortis Healthcare", "Max Super Speciality Hospital, Saket"])
def test_names(raw):
    assert parse_youtube_url(raw) == NameRef(name=raw)
