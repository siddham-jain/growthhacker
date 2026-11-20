from collections.abc import Sequence

from app.ports.llm import PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.services.website_intel.extract.injection import InjectionScanner
from app.services.website_intel.inputs.urls import path_of
from app.services.website_intel.models import NavItem, TemplateSummary, UrlCandidate
from app.services.website_intel.profile.llm import LlmGateway
from app.services.website_intel.profile.schemas import PageSelectionOutput

ANCHOR_CHARS = 80
MAX_NAV_LINES = 120
MAX_TEMPLATE_LINES = 40


class LlmPageSelector:
    """phase b on the low tier; the crawler re-checks ids, caps and tops up by score (plan 03 §5.8)."""

    def __init__(self, llm: LlmGateway, scanner: InjectionScanner | None = None) -> None:
        self._llm = llm
        self._scanner = scanner or InjectionScanner()

    def _clean(self, text: str) -> str:
        return self._scanner.scan(text[:ANCHOR_CHARS]).text.replace("|", "/").replace("\n", " ")

    async def select(
        self,
        *,
        candidates: Sequence[UrlCandidate],
        mandatory: Sequence[UrlCandidate],
        budget: int,
        nav: Sequence[NavItem],
        templates: Sequence[TemplateSummary],
        business_name: str | None,
    ) -> list[str] | None:
        if not self._llm.available or not candidates or budget <= 0:
            return None
        ids = {f"U{index + 1}": candidate.url for index, candidate in enumerate(candidates)}
        lines = [
            " | ".join(
                [
                    key,
                    path_of(candidate.url),
                    candidate.page_type.value,
                    candidate.template_key or "-",
                    self._clean(candidate.anchor) or "-",
                    f"{candidate.score:.2f}",
                    candidate.lastmod.date().isoformat() if candidate.lastmod else "-",
                ]
            )
            for key, candidate in zip(ids, candidates, strict=True)
        ]
        template_lines = [
            f"{summary.template_key} x{summary.count}"
            + (f" ({len(summary.location_values)} locations)" if summary.location_values else "")
            for summary in templates[:MAX_TEMPLATE_LINES]
        ]
        nav_lines = [
            f"{'  ' * (item.depth - 1)}{self._clean(item.label)} -> {path_of(item.url)}"
            for item in nav
            if item.depth <= 2
        ][:MAX_NAV_LINES]
        prompt = PromptInput(
            trusted=[
                TrustedBlock(
                    label="task",
                    text=f"Business name guess: {business_name or 'unknown'}\n"
                    f"Pick at most {budget} ids.\n"
                    f"Already chosen: {', '.join(path_of(item.url) for item in mandatory) or 'none'}",
                )
            ],
            untrusted=[
                UntrustedBlock(label="candidates", source="url_inventory", text="\n".join(lines)),
                UntrustedBlock(
                    label="templates", source="url_inventory", text="\n".join(template_lines) or "none"
                ),
                UntrustedBlock(label="navigation", source="site_nav", text="\n".join(nav_lines) or "none"),
            ],
        )
        output = await self._llm.structured(
            TaskId.website_page_selection, PageSelectionOutput, prompt, max_output_tokens=2048
        )
        if output is None:
            return None
        return [ids[pick.id] for pick in output.picks if pick.id in ids][:budget]
