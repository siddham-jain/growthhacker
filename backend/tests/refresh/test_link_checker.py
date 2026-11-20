from datetime import timedelta

import httpx
import pytest
import respx

from app.core.clock import FrozenClock
from app.core.config import RefreshSettings
from app.services.refresh.links.checker import FIRST_5XX, LinkChecker, is_public_address
from app.services.refresh.links.health import CachedLink, LinkHealthService
from app.services.refresh.links.status import LinkStatus, LinkStatusClass
from tests.refresh.fixtures import NOW

SETTINGS = RefreshSettings(link_domain_interval_s=0.0)
PUBLIC = "93.184.216.34"


def resolver_for(mapping: dict[str, str]):
    async def resolve(host: str) -> list[str]:
        return [mapping.get(host, PUBLIC)]

    return resolve


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as http:
        yield http


def checker(client, clock, mapping=None, own=frozenset()):
    return LinkChecker(
        client, SETTINGS, clock, own_domains=own, resolver=resolver_for(mapping or {}), sleep=no_sleep
    )


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/robots\.txt$").respond(404)
        yield router


async def test_ok_dead_and_blocked(mock, client, clock) -> None:
    mock.head("https://site.example/ok").respond(200)
    mock.head("https://site.example/gone").respond(404)
    mock.head("https://www.linkedin.com/company/x").respond(999)
    probe = checker(client, clock)
    assert (await probe.probe("https://site.example/ok")).status_class is LinkStatusClass.ok
    dead = await probe.probe("https://site.example/gone")
    assert (dead.status_class, dead.http_status) == (LinkStatusClass.dead, 404)
    blocked = await probe.probe("https://www.linkedin.com/company/x")
    assert blocked.status_class is LinkStatusClass.unverifiable


async def test_head_not_allowed_falls_back_to_get(mock, client, clock) -> None:
    mock.head("https://site.example/page").respond(405)
    mock.get("https://site.example/page").respond(200, text="hello")
    assert (
        await checker(client, clock).probe("https://site.example/page")
    ).status_class is LinkStatusClass.ok


async def test_permanent_redirect_to_another_site_is_info_only(mock, client, clock) -> None:
    mock.head("https://twitter.com/apollo").respond(301, headers={"location": "https://x.com/apollo"})
    status = await checker(client, clock).probe("https://twitter.com/apollo")
    assert status.status_class is LinkStatusClass.redirect
    assert status.final_url == "https://x.com/apollo"


async def test_shortener_chain_is_followed(mock, client, clock) -> None:
    mock.head("https://bit.ly/abc").respond(302, headers={"location": "https://site.example/landing"})
    mock.head("https://site.example/landing").respond(200)
    status = await checker(client, clock).probe("https://bit.ly/abc")
    assert status.status_class is LinkStatusClass.ok


async def test_server_errors_need_two_probes_a_day_apart(mock, client, clock) -> None:
    mock.head("https://site.example/flaky").respond(503)
    probe = checker(client, clock)
    first = await probe.probe("https://site.example/flaky")
    assert (first.status_class, first.error) == (LinkStatusClass.unverifiable, FIRST_5XX)
    clock.advance(timedelta(hours=25))
    second = await probe.probe("https://site.example/flaky", previous=first)
    assert second.status_class is LinkStatusClass.dead


async def test_timeouts_are_never_called_broken(mock, client, clock) -> None:
    mock.head("https://slow.example/").mock(side_effect=httpx.ReadTimeout("slow"))
    assert (
        await checker(client, clock).probe("https://slow.example/")
    ).status_class is LinkStatusClass.unverifiable


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "10.0.0.5", "100.64.1.1", "::1", "::ffff:127.0.0.1"]
)
async def test_internal_addresses_are_never_requested(mock, client, clock, address) -> None:
    route = mock.head("https://internal.example/admin").respond(200)
    status = await checker(client, clock, {"internal.example": address}).probe(
        "https://internal.example/admin"
    )
    assert status.status_class is LinkStatusClass.unverifiable
    assert not route.called


async def test_redirects_into_private_space_are_refused(mock, client, clock) -> None:
    mock.head("https://public.example/go").respond(302, headers={"location": "http://rebind.example/meta"})
    hidden = mock.head("http://rebind.example/meta").respond(200)
    status = await checker(client, clock, {"rebind.example": "169.254.169.254"}).probe(
        "https://public.example/go"
    )
    assert status.status_class is LinkStatusClass.unverifiable
    assert not hidden.called


async def test_robots_disallow_means_unverifiable(client, clock) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("https://private.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /")
        route = router.head("https://private.example/page").respond(404)
        status = await checker(client, clock).probe("https://private.example/page")
    assert status.status_class is LinkStatusClass.unverifiable
    assert not route.called


async def test_soft_404_on_the_clients_own_site(mock, client, clock) -> None:
    mock.head("https://clinic.example/doctors/dr-x").respond(
        302, headers={"location": "https://clinic.example/"}
    )
    mock.head("https://clinic.example/old/page").respond(200)
    mock.get("https://clinic.example/old/page").respond(200, text="<title>Page not found</title>")
    probe = checker(client, clock, own=frozenset({"clinic.example"}))
    assert (await probe.probe("https://clinic.example/doctors/dr-x")).status_class is LinkStatusClass.soft_404
    assert (await probe.probe("https://clinic.example/old/page")).status_class is LinkStatusClass.soft_404


async def test_malformed_links_are_caught_without_network(client, clock) -> None:
    status = await checker(client, clock).probe(
        "https://www.facebook.com/https://www.facebook.com/TheApolloHospitals/"
    )
    assert status.status_class is LinkStatusClass.malformed
    assert status.repaired_url == "https://www.facebook.com/TheApolloHospitals/"


def test_public_address_check() -> None:
    assert is_public_address(PUBLIC)
    assert not is_public_address("192.168.1.1")
    assert not is_public_address("fe80::1")


class MemoryCache:
    def __init__(self, entries=None) -> None:
        self.entries: dict[str, CachedLink] = dict(entries or {})

    async def get_many(self, keys):
        return {key: self.entries[key] for key in keys if key in self.entries}

    async def put_many(self, entries):
        for entry in entries:
            self.entries[entry.status.url_normalised] = entry


class CountingProbe:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def probe(self, url, previous=None):
        self.calls.append(url)
        return LinkStatus(url.removeprefix("https://"), LinkStatusClass.ok, NOW, http_status=200)


async def test_health_service_uses_cache_budget_and_probes_repairs() -> None:
    fresh = CachedLink(
        LinkStatus("cached.example/a", LinkStatusClass.dead, NOW, http_status=404), NOW + timedelta(days=1)
    )
    cache = MemoryCache({"cached.example/a": fresh})
    probe = CountingProbe()
    settings = RefreshSettings(link_max_probes=2)
    service = LinkHealthService(settings, FrozenClock(NOW), probe, cache)
    report = await service.check(
        [
            ("https://cached.example/a", 1.0),
            ("https://x.example/1", 3.0),
            ("https://x.example/2", 2.0),
            ("https://x.example/3", 0.5),
            ("https://www.facebook.com/https://www.facebook.com/TheApolloHospitals/", 0.1),
        ]
    )
    assert report.statuses["cached.example/a"].status_class is LinkStatusClass.dead
    assert report.statuses["facebook.com/https://www.facebook.com/TheApolloHospitals"].status_class is (
        LinkStatusClass.malformed
    )
    assert probe.calls == ["https://x.example/1", "https://x.example/2"]
    assert report.deferred == 2
    assert "x.example/1" in cache.entries
