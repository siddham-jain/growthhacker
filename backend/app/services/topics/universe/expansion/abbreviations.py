import regex

from app.domain.topics import TopicAlias, TopicRef
from app.services.topics.universe.aliases import make_alias, norm_key
from app.services.topics.universe.expansion.base import ExpansionContext, expander_registry

PARENTHESISED = regex.compile(r"\(([^()]{2,10})\)")
SHORT_FORM = regex.compile(r"^[A-Z][A-Za-z0-9-]{1,5}$")
CLAUSE_BREAK = regex.compile(r"[.;:!?\n]")


def best_long_form(short: str, candidate: str) -> str | None:
    """schwartz & hearst (2003): shortest suffix of candidate whose letters spell the short form."""
    short_index, long_index = len(short) - 1, len(candidate) - 1
    while short_index >= 0:
        char = short[short_index].lower()
        if not char.isalnum():
            short_index -= 1
            continue
        while long_index >= 0 and (
            candidate[long_index].lower() != char
            or (short_index == 0 and long_index > 0 and candidate[long_index - 1].isalnum())
        ):
            long_index -= 1
        if long_index < 0:
            return None
        long_index -= 1
        short_index -= 1
    start = candidate.rfind(" ", 0, long_index + 1) + 1
    long_form = candidate[start:].strip()
    if len(long_form) <= len(short) or short.lower() in long_form.lower().split():
        return None
    return long_form


def abbreviation_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for match in PARENTHESISED.finditer(text):
        short = match.group(1).strip()
        if not SHORT_FORM.match(short) or sum(char.isupper() for char in short) < 2:
            continue
        clause = CLAUSE_BREAK.split(text[: match.start()])[-1]
        words = clause.split()
        window = words[-min(len(short) + 5, len(short) * 2) :]
        long_form = best_long_form(short, " ".join(window))
        if long_form:
            pairs.append((long_form, short))
    return pairs


def _contains(haystack: str, needle: str) -> bool:
    hay, pin = haystack.split(), needle.split()
    return any(hay[index : index + len(pin)] == pin for index in range(len(hay) - len(pin) + 1))


@expander_registry.register("abbreviation")
class AbbreviationExpander:
    name = "abbreviation"

    def expand(self, ref: TopicRef, ctx: ExpansionContext) -> list[TopicAlias]:
        anchors = [alias for alias in ref.aliases if alias.kind in {"canonical", "client_alias"}]
        exempt = ctx.lexicons.healthcare_exempt_acronyms if ctx.domain_kind == "healthcare" else frozenset()
        aliases: list[TopicAlias] = []
        for text in ctx.profile_texts:
            for long_form, short in abbreviation_pairs(text):
                long_key = norm_key(long_form)
                contained = [alias for alias in anchors if _contains(long_key, alias.norm_key)]
                anchor = max(contained, key=lambda alias: len(alias.norm_key.split()), default=None)
                if anchor is None:
                    continue
                ambiguous = (
                    short.upper() in ctx.lexicons.ambiguous_acronyms - exempt
                    or ctx.corpus.share(short.lower()) > ctx.settings.ambig_df
                )
                alias = make_alias(
                    short,
                    kind="abbreviation",
                    origin="profile_extracted",
                    status="confirmed",
                    anchor=anchor.text,
                    weight=1.0,
                    lang=anchor.lang,
                    ambiguous=ambiguous,
                )
                if alias is not None:
                    aliases.append(alias)
        return aliases
