from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

FieldKind = Literal["choice", "multi", "scale", "bool", "text"]
SECONDS_COLUMN = "seconds_spent"


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    kind: FieldKind
    options: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class PackSchema:
    """what a pack shows, what it must never show, and the csv it exports."""

    kind: str
    title: str
    id_column: str
    fields: tuple[FormField, ...]
    hidden_fields: frozenset[str]
    header_html: str

    @property
    def csv_columns(self) -> tuple[str, ...]:
        return (self.id_column, *(f.name for f in self.fields), SECONDS_COLUMN)

    def field(self, name: str) -> FormField:
        for item in self.fields:
            if item.name == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True)
class PackItem:
    # body_html is built by the kind renderer from `visible` only; hidden data never reaches it
    item_id: str
    visible: Mapping[str, Any]
    body_html: str
