import pytest

from app.services.website_intel.extract.contact import (
    full_phone,
    meaningful_address,
    meaningful_label,
    valid_email,
)


@pytest.mark.parametrize(
    ("raw", "regions", "expected"),
    [
        ("+91-88-0000-1068", (), "+918800001068"),
        ("040 4344 1066", ("IN",), "+914043441066"),
        ("040 4344 1066", ("US", "IN"), "+914043441066"),
        ("+91", (), None),
        ("1068", ("IN",), None),
        ("Submit", ("IN",), None),
    ],
)
def test_full_phone_needs_a_complete_valid_number(raw, regions, expected):
    assert full_phone(raw, *regions) == expected


def test_email_labels_and_addresses():
    assert valid_email("info@medanta.org") and not valid_email("info@") and not valid_email("+91")
    assert meaningful_label("Book Appointment") and meaningful_label("संपर्क करें")
    assert not any(
        meaningful_label(text) for text in ("Submit", "submit »", "+91", "1068", "", "Go", "Get OTP")
    )
    assert meaningful_address("CH Baktawar Singh Rd, Sector 38, Gurugram")
    assert not any(meaningful_address(text) for text in ("+91", "Submit", "122001", None))
