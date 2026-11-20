from datetime import UTC, datetime, timedelta

import pytest

from app.evaluation.packs.exports import PackCsvError, read_pack_csv, write_pack_csv
from app.evaluation.packs.ids import batch_uuid
from app.evaluation.packs.participants import participant_id
from app.evaluation.packs.renderer import PackExpired, PackRenderer, pack_order
from app.evaluation.packs.schema import FormField, PackItem, PackSchema

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
SCHEMA = PackSchema(
    kind="demo",
    title="Demo pack",
    id_column="item_id",
    fields=(
        FormField("answer", "Answer", "choice", ("a", "b")),
        FormField("note", "Note", "text", required=False),
    ),
    hidden_fields=frozenset({"secret"}),
    header_html="<p>rules</p>",
)


def items(n: int = 4) -> list[PackItem]:
    return [PackItem(item_id=f"i{k}", visible={"title": f"t{k}"}, body_html=f"<p>t{k}</p>") for k in range(n)]


def render(**overrides) -> str:
    values = {
        "pack_id": "p1",
        "participant": "mira",
        "seed": 7,
        "generated_at": NOW,
        "expires_at": NOW + timedelta(days=20),
    } | overrides
    return PackRenderer(SCHEMA, batch_size=2).render(items(), **values)


def test_order_is_reproducible_per_participant_and_differs_between_them() -> None:
    ids = [f"i{k}" for k in range(30)]
    assert pack_order(ids, 7, "mira") == pack_order(ids, 7, "mira")
    assert pack_order(ids, 7, "mira") != pack_order(ids, 7, "marketer")
    assert sorted(pack_order(ids, 7, "mira")) == sorted(ids)


def test_pack_embeds_expiry_batches_and_export() -> None:
    page = render()
    assert '"expires_at": "2026-12-10T12:00:00+00:00"' in page
    assert "This pack has expired" in page
    assert '"batch_size": 2' in page
    assert "Export CSV" in page
    assert "localStorage" in page
    assert '<main id="pack" hidden>' in page


def test_expired_pack_refuses_to_render() -> None:
    with pytest.raises(PackExpired):
        render(expires_at=NOW)


def test_hidden_fields_cannot_reach_the_page() -> None:
    leaky = [PackItem(item_id="x", visible={"secret": 3.14}, body_html="<p>x</p>")]
    with pytest.raises(ValueError, match="hidden"):
        PackRenderer(SCHEMA, 2).render(
            leaky, pack_id="p", participant="a", seed=1, generated_at=NOW, expires_at=NOW + timedelta(days=1)
        )


def test_csv_round_trip_and_validation() -> None:
    rows = [{"item_id": "i1", "answer": "a", "note": "has, comma", "seconds_spent": "12"}]
    text = write_pack_csv(rows, SCHEMA)
    assert read_pack_csv(text, SCHEMA) == rows
    with pytest.raises(PackCsvError, match="missing columns"):
        read_pack_csv("item_id,answer\ni1,a\n", SCHEMA)
    with pytest.raises(PackCsvError, match="twice"):
        read_pack_csv(text + "i1,b,,\n", SCHEMA)


def test_participant_ids_are_stable_and_batch_ids_sort_by_time() -> None:
    assert participant_id(" Mira ") == participant_id("mira")
    assert participant_id("mira") != participant_id("marketer")
    with pytest.raises(ValueError):
        participant_id("  ")
    earlier, later = batch_uuid(NOW), batch_uuid(NOW + timedelta(seconds=1))
    assert earlier < later
    assert earlier.version == 7
