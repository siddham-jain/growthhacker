from app.domain.topics import TopicAlias, TopicRef
from app.services.topics.universe.aliases import make_alias
from app.services.topics.universe.expansion.base import ExpansionContext, expander_registry


@expander_registry.register("transliteration")
class TransliterationExpander:
    """romanised form of every devanagari alias, so hinglish spellings are visible and confirmable."""

    name = "transliteration"

    def expand(self, ref: TopicRef, ctx: ExpansionContext) -> list[TopicAlias]:
        known = {alias.norm_key for alias in ref.aliases}
        aliases: list[TopicAlias] = []
        for source in ref.aliases:
            if source.script != "Deva" or not source.phonetic_key or source.status == "rejected":
                continue
            alias = make_alias(
                source.phonetic_key,
                kind="transliteration",
                origin="rule",
                status=source.status,
                anchor=source.anchor_text,
                weight=source.weight,
                lang="hi-Latn",
                ambiguous=source.ambiguous,
            )
            if alias is not None and alias.norm_key not in known:
                known.add(alias.norm_key)
                aliases.append(alias)
        return aliases
