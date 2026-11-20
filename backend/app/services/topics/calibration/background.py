import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

BACKGROUND_FILE = Path(__file__).parent / "background_v1.yaml"


@dataclass(frozen=True)
class BackgroundSet:
    version: str
    titles: tuple[str, ...]


@lru_cache(maxsize=1)
def load_background() -> BackgroundSet:
    raw = BACKGROUND_FILE.read_text("utf-8")
    data = yaml.safe_load(raw)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return BackgroundSet(version=f"bg{data['version']}+{digest}", titles=tuple(data["titles"]))
