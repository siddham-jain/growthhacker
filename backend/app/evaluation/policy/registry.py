from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.registry import DuplicateRegistrationError, UnknownRegistrationError

Guardrail = Literal["G1", "G2", "G3", "G4", "G5", "G6"]
Cell = Literal["OK", "COND", "FLAG", "n/a"]
Source = Literal[
    "yt_data_public",
    "yt_analytics_oauth",
    "website",
    "web_search",
    "client_input",
    "spotlit_ai_tracking",
    "llm",
    "internal",
]
# plan 12 §3.6 plus difference, dispersion and statistical_test, which the matrix rows need to stay honest
Maths = Literal[
    "raw",
    "count",
    "sum",
    "difference",
    "mean",
    "median",
    "ratio",
    "quantile",
    "dispersion",
    "interpolation",
    "threshold_flag",
    "rank",
    "statistical_test",
    "lexical_match",
    "embedding_similarity",
    "llm_judgement",
    "image_simple",
    "text_generation",
]
Scope = Literal["own", "competitor", "niche"]

GUARDRAILS: tuple[Guardrail, ...] = ("G1", "G2", "G3", "G4", "G5", "G6")
GUARDRAIL_TEXT: dict[Guardrail, str] = {
    "G1": "API data plus simple maths (counts, sums, medians, means, ratios, sorted lists)",
    "G2": "never merge YouTube data with other sources into one metric (side by side is fine)",
    "G3": "no revenue or monetisation estimates",
    "G4": "no audience demographics, no inferring a video's content category",
    "G5": "refresh or delete stored API data within 30 days (act at 28)",
    "G6": "thumbnails: simple traits only, no face recognition",
}
CELL_TEXT: dict[Cell, str] = {
    "OK": "complies",
    "COND": "complies under the stated condition",
    "FLAG": "complies by our reading but needs reviewer sign-off",
    "n/a": "guardrail not applicable",
}


class FeatureSpec(BaseModel):
    """one computed feature and its guardrail checks; a row of the week-1 policy matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    key: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    owner: str = Field(pattern=r"^\d{2}(/\d{2})*$")
    name: str = Field(min_length=1)
    sources: tuple[Source, ...] = Field(min_length=1)
    maths: tuple[Maths, ...] = Field(min_length=1)
    scope: tuple[Scope, ...] = ()
    displayed_as_metric: bool
    stored: bool
    retention_days: int | None = Field(default=None, ge=1)
    checks: dict[Guardrail, Cell]
    condition: str
    review_note: str | None = None
    covers: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _consistent(self) -> "FeatureSpec":
        if set(self.checks) != set(GUARDRAILS):
            raise ValueError(f"{self.key}: checks must cover exactly {', '.join(GUARDRAILS)}")
        if self.flagged and not (self.review_note or "").strip():
            raise ValueError(f"{self.key}: a FLAG cell needs a review note")
        if not self.flagged and self.review_note is not None:
            raise ValueError(f"{self.key}: review notes belong to FLAG rows only")
        if self.stored and "yt_data_public" in self.sources and self.retention_days is None:
            raise ValueError(f"{self.key}: stored public API data needs a retention period")
        if not self.condition.strip():
            raise ValueError(f"{self.key}: every row states its condition or mitigation")
        return self

    @property
    def flagged(self) -> bool:
        return "FLAG" in self.checks.values()

    @property
    def flagged_guardrails(self) -> list[Guardrail]:
        return [g for g in GUARDRAILS if self.checks[g] == "FLAG"]


class FeatureRegistry:
    def __init__(self) -> None:
        self._by_key: dict[str, FeatureSpec] = {}
        self._numbers: dict[int, str] = {}

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        if spec.key in self._by_key:
            raise DuplicateRegistrationError(f"feature_registry: '{spec.key}' is already registered")
        if spec.number in self._numbers:
            raise DuplicateRegistrationError(
                f"feature_registry: row {spec.number} is taken by '{self._numbers[spec.number]}'"
            )
        self._by_key[spec.key] = spec
        self._numbers[spec.number] = spec.key
        return spec

    def get(self, key: str) -> FeatureSpec:
        try:
            return self._by_key[key]
        except KeyError:
            raise UnknownRegistrationError(f"feature_registry: '{key}' is not registered") from None

    def all(self) -> list[FeatureSpec]:
        return sorted(self._by_key.values(), key=lambda spec: spec.number)

    def flagged(self) -> list[FeatureSpec]:
        return [spec for spec in self.all() if spec.flagged]

    def problems(self) -> list[str]:
        numbers = sorted(self._numbers)
        if numbers != list(range(1, len(numbers) + 1)):
            return [f"row numbers must run 1..{len(numbers)} without gaps, got {numbers}"]
        return []

    def __len__(self) -> int:
        return len(self._by_key)


feature_registry = FeatureRegistry()
