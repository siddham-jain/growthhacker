from typing import Literal

WebSearchErrorKind = Literal["credit_limit", "rate_limited", "auth", "bad_request", "timeout", "unavailable"]


class WebSearchError(RuntimeError):
    # callers disable web search for the rest of the run on credit_limit or auth (plan 03 §8 #58)
    def __init__(self, kind: WebSearchErrorKind, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail

    @property
    def disables_provider(self) -> bool:
        return self.kind in {"credit_limit", "auth"}
