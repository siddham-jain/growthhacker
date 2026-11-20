import re

import pytest
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import conv

import app.models
from app.models.base import Base, Zone
from app.retention.rules import API_DATA_CLASSES, RetentionAction, retention_registry, rules_for_table

TABLES: dict[str, Table] = dict(Base.metadata.tables)
ZONES = {mapper.class_.__tablename__: mapper.class_.__zone__ for mapper in Base.registry.mappers}
TENANT_COLUMNS = {"workspace_id", "client_id"}
YOUTUBE_SHARED_PREFIX = "yt_"
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def test_models_package_registers_every_module() -> None:
    assert len(TABLES) == len(ZONES) > 100
    assert app.models.Base is Base


def test_every_orm_table_has_a_retention_rule() -> None:
    covered = {rule.table for rule in retention_registry.all().values()}
    assert set(TABLES) - covered == set()
    assert covered - set(TABLES) == set()


@pytest.mark.parametrize("rule", list(retention_registry.all().values()), ids=lambda rule: rule.name)
def test_retention_rule_columns_exist(rule) -> None:
    table = TABLES[rule.table]
    if rule.clock and IDENTIFIER.match(rule.clock):
        assert rule.clock in table.columns, f"{rule.name}: clock column {rule.clock} missing"
    for column in rule.redact_set:
        assert column in table.columns, f"{rule.name}: redact column {column} missing"
        assert table.columns[column].nullable or rule.redact_set[column] != "NULL"
    if rule.parent:
        assert rule.parent in TABLES


def test_api_data_tables_never_keep_forever() -> None:
    for name in TABLES:
        rules = rules_for_table(name)
        if any(rule.data_class in API_DATA_CLASSES for rule in rules):
            assert all(rule.action is not RetentionAction.keep for rule in rules), name


def test_shared_tables_have_no_tenant_columns() -> None:
    for name, zone in ZONES.items():
        if zone is Zone.shared:
            assert not TENANT_COLUMNS & set(TABLES[name].columns.keys()), name


def test_tenant_tables_carry_scope_and_composite_client_fk() -> None:
    for name, zone in ZONES.items():
        if zone is not Zone.tenant or name == "clients":
            continue
        table = TABLES[name]
        assert set(table.columns.keys()) >= TENANT_COLUMNS, name
        assert not table.columns["workspace_id"].nullable and not table.columns["client_id"].nullable, name
        composite = [
            fk
            for fk in table.foreign_key_constraints
            if fk.referred_table.name == "clients"
            and [c.name for c in fk.columns] == ["workspace_id", "client_id"]
        ]
        assert composite, f"{name} lacks the (workspace_id, client_id) -> clients foreign key"


def test_tenant_tables_never_reference_the_youtube_cache() -> None:
    for name, zone in ZONES.items():
        if zone in (Zone.tenant, Zone.workspace):
            targets = {fk.column.table.name for fk in TABLES[name].foreign_keys}
            assert not {target for target in targets if target.startswith(YOUTUBE_SHARED_PREFIX)}, name


def test_no_category_or_topic_details_columns() -> None:
    for name, table in TABLES.items():
        assert not {"category_id", "topic_details", "topicdetails"} & set(table.columns.keys()), name


def test_explicit_object_names_fit_postgres_limit() -> None:
    # convention-generated names are truncated deterministically by sqlalchemy; hand-written ones must fit
    for table in TABLES.values():
        for item in [*table.indexes, *table.constraints]:
            if item.name is not None and not isinstance(item.name, conv):
                assert len(str(item.name)) <= 63, item.name


def test_timestamps_are_timezone_aware() -> None:
    for name, table in TABLES.items():
        for column in table.columns:
            if column.type.__class__.__name__ in {"TIMESTAMP", "DateTime"}:
                assert column.type.timezone, f"{name}.{column.name}"


def test_jsonb_columns_have_defaults_or_are_nullable() -> None:
    for name, table in TABLES.items():
        for column in table.columns:
            if isinstance(column.type, JSONB) and not column.nullable and column.server_default is None:
                assert not column.primary_key, f"{name}.{column.name}"
