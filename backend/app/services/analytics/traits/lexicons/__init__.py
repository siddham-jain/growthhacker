from functools import cache
from importlib import resources
from typing import Any

import yaml


@cache
def load_lexicon(name: str) -> dict[str, Any]:
    text = resources.files(__name__).joinpath(f"{name}.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "version" not in data:
        raise ValueError(f"lexicon '{name}' must be a mapping with a version")
    return data
