import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

NA = "n/a"


def cell(value: Any) -> str:
    if value is None:
        return NA
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(cell(h) for h in headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def num(value: float | None, digits: int = 2) -> str:
    return NA if value is None else f"{value:.{digits}f}"


def pct(value: float | None, digits: int = 1) -> str:
    return NA if value is None else f"{100 * value:.{digits}f}%"


def interval(bounds: tuple[float, float] | None, digits: int = 2, *, as_pct: bool = False) -> str:
    if bounds is None:
        return NA
    render = (lambda v: pct(v, digits - 1)) if as_pct else (lambda v: num(v, digits))
    return f"[{render(bounds[0])}, {render(bounds[1])}]"


def estimate_with_ci(
    value: float | None, bounds: tuple[float, float] | None, digits: int = 2, *, as_pct: bool = False
) -> str:
    head = pct(value, digits - 1) if as_pct else num(value, digits)
    return head if bounds is None else f"{head} {interval(bounds, digits, as_pct=as_pct)}"


def write_report(path: Path, markdown: str, payload: dict[str, Any] | None = None) -> list[Path]:
    """writes the markdown and, when given, a sibling json with the same stem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown if markdown.endswith("\n") else markdown + "\n", encoding="utf-8")
    written = [path]
    if payload is not None:
        json_path = path.with_suffix(".json")
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", "utf-8")
        written.append(json_path)
    return written
