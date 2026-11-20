from collections import Counter
from collections.abc import Iterable

from app.domain.topics import PreparedVideoText
from app.services.topics.text.tokenize import tokenize
from app.services.topics.universe.expansion.base import CorpusFrequencies


def corpus_frequencies(videos: Iterable[PreparedVideoText]) -> CorpusFrequencies:
    df: Counter[str] = Counter()
    documents = 0
    for video in videos:
        fields = video.fields
        text = "\n".join(
            [
                fields.title,
                *fields.chapters,
                fields.description_head,
                fields.description_rest,
                *fields.tags,
                *fields.hashtags,
            ]
        )
        df.update({token.stem for token in tokenize(text)})
        documents += 1
    return CorpusFrequencies(documents=documents, df=dict(df))
