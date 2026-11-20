import io
import logging
from collections.abc import Iterator

import httpx
import pytest

from app.core.logging import RedactingFilter, install_stdlib_redaction, redact_text

KEY = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE12"


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    root_level = root.level
    # configure_logging runs the root logger at info, where httpx prints every request url
    root.setLevel(logging.INFO)
    levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore", "thirdparty")}
    logging.getLogger("thirdparty").setLevel(logging.INFO)
    install_stdlib_redaction()
    yield stream
    root.removeHandler(handler)
    root.setLevel(root_level)
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


async def test_httpx_request_lines_never_print_the_youtube_key(captured: io.StringIO) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://www.googleapis.com/youtube/v3/videos", params={"key": KEY})
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert KEY not in captured.getvalue()


def test_stdlib_records_are_redacted_like_structlog_events(captured: io.StringIO) -> None:
    logging.getLogger("thirdparty").warning("GET %s failed", f"https://h/v3?part=id&key={KEY}&token=abc123")
    try:
        raise RuntimeError(f"boom https://h/?access_token={KEY}")
    except RuntimeError:
        logging.getLogger("thirdparty").exception("request failed")
    output = captured.getvalue()
    assert KEY not in output and "abc123" not in output
    assert "part=id" in output


def test_query_secrets_are_redacted_but_ordinary_params_kept() -> None:
    text = redact_text("https://x/cb?code=4/0Ab-secret&state=xyz&client_secret=s3cr3t&page=2")
    assert "4/0Ab-secret" not in text and "s3cr3t" not in text
    assert "page=2" in text


def test_filter_is_installed_once_per_handler(captured: io.StringIO) -> None:
    install_stdlib_redaction()
    handler = logging.getLogger().handlers[-1]
    assert sum(isinstance(item, RedactingFilter) for item in handler.filters) == 1
