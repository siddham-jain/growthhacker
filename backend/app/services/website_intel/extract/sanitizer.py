import re
import unicodedata

from selectolax.lexbor import LexborHTMLParser, LexborNode

DROPPED_TAGS = ("script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "form")
NON_PROSE_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
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
_CODE_PATTERNS = (
    re.compile(
        r"<\s*/?\s*(?:svg|path|style|script|noscript|defs|symbol|rect|circle|polygon|polyline|div|span|iframe)[\s>/]",
        re.IGNORECASE,
    ),
    re.compile(r"@(?:media|font-face|keyframes|import|supports|charset)\b", re.IGNORECASE),
    re.compile(r"[\w\-.#\])*]\s*\{[^{}]*:[^{}]*[;}]"),
    re.compile(
        r"\b(?:fill|stroke|color|background(?:-color)?|border-color)\s*:\s*#[0-9a-f]{3,8}\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:stroke-(?:width|linecap|linejoin|dasharray|miterlimit)|fill-(?:rule|opacity)|clip-rule|viewbox|xmlns)"
        r"\s*[:=]",
        re.IGNORECASE,
    ),
    # svg path data such as "M12 2C6.48 2 2 6.48" or "M0 0h24v24H0z"
    re.compile(r"\b[Mm]-?\d*\.?\d+(?:[\s,]*[LlHhVvCcSsQqTtAaZz]?[\s,]*-?\d*\.?\d+){4,}"),
    re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE),
    re.compile(r"data:[a-z]+/[\w+.-]+;base64,", re.IGNORECASE),
)
_CODE_SYMBOLS = re.compile(r"[{}<>=]")
CODE_SYMBOL_MIN = 6
CODE_SYMBOL_SHARE = 0.08


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_CHARS.sub("", text)
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n", "\n".join(lines)).strip()


def looks_like_code(text: str) -> bool:
    """markup, css, svg path data or symbol-dense text that is not prose; prices like ₹4,100/- pass."""
    if any(pattern.search(text) for pattern in _CODE_PATTERNS):
        return True
    symbols = len(_CODE_SYMBOLS.findall(text))
    return symbols >= CODE_SYMBOL_MIN and symbols >= CODE_SYMBOL_SHARE * len(text)


def drop_code_lines(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if not looks_like_code(line))


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


def inline_text(node: LexborNode) -> str:
    """text of a subtree joined by spaces, skipping script, style and svg (labels are read before stripping)."""
    parts: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.is_text_node:
            parts.append(current.text_content or "")
            continue
        if (current.tag or "") in NON_PROSE_TAGS or not (current.is_element_node or current.is_document_node):
            continue
        children = []
        child = current.child
        while child is not None:
            children.append(child)
            child = child.next
        stack.extend(reversed(children))
    return normalize_text(" ".join(parts))
