import trafilatura
from selectolax.lexbor import LexborHTMLParser

from app.services.website_intel.extract.sanitizer import block_text, normalize_text

MIN_MAIN_TEXT_CHARS = 400
MIN_BODY_SHARE = 0.15
CHROME_TAGS = frozenset({"nav", "header", "footer", "aside"})


class MainTextExtractor:
    """trafilatura first; selectolax body text when trafilatura keeps too little (plan 03 §5.7.3)."""

    def extract(self, tree: LexborHTMLParser, *, url: str) -> str:
        body_text = block_text(tree.body)
        extracted = trafilatura.extract(
            tree.html or "",
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=False,
            include_images=False,
            favor_recall=True,
            include_comments=False,
        )
        main = normalize_text(extracted or "")
        if len(main) >= max(MIN_MAIN_TEXT_CHARS, MIN_BODY_SHARE * len(body_text)):
            return main
        fallback = block_text(tree.body, skip_tags=CHROME_TAGS)
        return fallback if len(fallback) > len(main) else main
