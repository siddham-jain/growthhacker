import re
import unicodedata

from selectolax.lexbor import LexborHTMLParser, LexborNode

DROPPED_TAGS = ("script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "form")
CONSENT_ROOTS = (
    "#onetrust-banner-sdk",
    "#onetrust-consent-sdk",
    "#CybotCookiebotDialog",
    "#didomi-host",
    ".qc-cmp2-container",
    "#truste-consent-track",
    ".osano-cm-window",
    "[class*=cookie][class*=banner]",
)
HIDDEN_SELECTORS = ("[hidden]", "[aria-hidden=true]", ".sr-only", ".visually-hidden")
TOGGLED_HIDDEN_CLASSES = frozenset({"hidden", "d-none"})
# tailwind/bootstrap classes that re-show a "hidden" element at some breakpoint
_RESPONSIVE_SHOW = re.compile(
    r"^(?:(?:sm|md|lg|xl|2xl):(?:block|flex|grid|inline|inline-block|table)|d-(?:sm|md|lg|xl|xxl)-(?:block|flex|grid|inline|inline-block|table))$"
)
BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
HEADING_PREFIX = {"h1": "# ", "h2": "## ", "h3": "### "}
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)|font-size\s*:\s*0(?:px|em|rem)?\s*(?:;|$)",
    re.IGNORECASE,
)
_INVISIBLE_CHARS = re.compile("[​-‏⁠﻿‪-‮⁦-⁩]")
_INLINE_SPACE = re.compile(r"[ \t\r\f\v ]+")
_BLANK_LINES = re.compile(r"\n{2,}")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_CHARS.sub("", text)
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n", "\n".join(lines)).strip()


class DomSanitizer:
    """removes non-content, hidden and consent nodes in place (plan 03 §5.7.1)."""

    def strip(self, tree: LexborHTMLParser) -> None:
        for tag in DROPPED_TAGS:
            for node in tree.css(tag):
                node.decompose()
        for selector in (*HIDDEN_SELECTORS, *CONSENT_ROOTS):
            for node in tree.css(selector):
                node.decompose()
        for node in tree.css("[style]"):
            if _HIDDEN_STYLE.search(node.attributes.get("style") or ""):
                node.decompose()
        for node in tree.css(".hidden, .d-none"):
            classes = set((node.attributes.get("class") or "").split())
            if classes & TOGGLED_HIDDEN_CLASSES and not any(_RESPONSIVE_SHOW.match(name) for name in classes):
                node.decompose()
        self._drop_comments(tree)

    def _drop_comments(self, tree: LexborHTMLParser) -> None:
        root = tree.root
        if root is None:
            return
        comments = [node for node in root.traverse(include_text=True) if node.is_comment_node]
        for node in comments:
            node.decompose()


def block_text(node: LexborNode | None, *, skip_tags: frozenset[str] = frozenset()) -> str:
    """visible text with block structure kept as lines and h1-h3 marked as markdown headings."""
    if node is None:
        return ""
    parts: list[str] = []
    stack: list[tuple[LexborNode, bool]] = [(node, False)]
    while stack:
        current, closing = stack.pop()
        tag = current.tag or ""
        if closing:
            if tag in BLOCK_TAGS:
                parts.append("\n")
            continue
        if current.is_text_node:
            parts.append(current.text_content or "")
            continue
        if not current.is_element_node and not current.is_document_node:
            continue
        if tag in skip_tags:
            continue
        if tag in BLOCK_TAGS:
            parts.append("\n")
            parts.append(HEADING_PREFIX.get(tag, ""))
        stack.append((current, True))
        children = []
        child = current.child
        while child is not None:
            children.append(child)
            child = child.next
        stack.extend((child, False) for child in reversed(children))
    return normalize_text("".join(parts))
