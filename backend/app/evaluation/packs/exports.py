import csv
import io

from app.evaluation.packs.schema import PackSchema


class PackCsvError(ValueError):
    pass


def read_pack_csv(text: str, schema: PackSchema) -> list[dict[str, str]]:
    """rows of a pack's exported csv, keyed by column; rejects a csv from another pack kind."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    columns = tuple(reader.fieldnames or ())
    missing = [c for c in schema.csv_columns if c not in columns]
    if missing:
        raise PackCsvError(f"csv is missing columns {missing}; export it from a {schema.kind} pack")
    rows = [{column: (row.get(column) or "").strip() for column in schema.csv_columns} for row in reader]
    seen: set[str] = set()
    for line, row in enumerate(rows, 2):
        item_id = row[schema.id_column]
        if not item_id:
            raise PackCsvError(f"line {line}: {schema.id_column} is empty")
        if item_id in seen:
            raise PackCsvError(f"line {line}: {item_id} appears twice")
        seen.add(item_id)
    return rows


def write_pack_csv(rows: list[dict[str, str]], schema: PackSchema) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(schema.csv_columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in schema.csv_columns})
    return buffer.getvalue()
