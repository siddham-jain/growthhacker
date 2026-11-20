import math

from app.core.config import LLMPrice
from app.ports.llm import TokenUsage

PER_TOKEN = 1 / 1_000_000


def estimate_tokens(text: str) -> int:
    # conservative bytes/3 estimate; non-english text inflates token counts
    return math.ceil(len(text.encode("utf-8")) / 3.0)


class CostCalculator:
    def __init__(self, prices: dict[str, LLMPrice]) -> None:
        if not prices:
            raise ValueError("llm.prices must list at least one model")
        self._prices = prices
        self._ceiling = LLMPrice(
            input=max(price.input for price in prices.values()),
            output=max(price.output for price in prices.values()),
            cache_write_5m=max(price.cache_write_5m for price in prices.values()),
            cache_read=max(price.cache_read for price in prices.values()),
        )

    def price(self, model: str) -> LLMPrice | None:
        for known in sorted(self._prices, key=len, reverse=True):
            if model == known or model.startswith(f"{known}-"):
                return self._prices[known]
        return None

    def cost(self, model: str, usage: TokenUsage) -> float | None:
        # output tokens already include thinking tokens; none means the price is missing, never zero
        price = self.price(model)
        if price is None:
            return None
        return (
            usage.input_tokens * price.input
            + usage.cache_write_tokens * price.cache_write_5m
            + usage.cache_read_tokens * price.cache_read
            + usage.output_tokens * price.output
        ) * PER_TOKEN

    def ceiling_cost(self, usage: TokenUsage) -> float:
        # budget proxy for models without a configured price
        price = self._ceiling
        return (
            (usage.input_tokens + usage.cache_write_tokens + usage.cache_read_tokens) * price.input
            + usage.output_tokens * price.output
        ) * PER_TOKEN

    def estimate(self, model: str, input_tokens: int, max_output_tokens: int) -> float:
        # worst case: nothing cached and the whole output budget used; unknown models use the dearest price
        price = self.price(model) or self._ceiling
        return (input_tokens * price.input + max_output_tokens * price.output) * PER_TOKEN
