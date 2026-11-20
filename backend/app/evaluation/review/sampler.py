import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import timedelta

import numpy as np

from app.core.config import ReviewSettings
from app.evaluation.labelling.sampler import largest_remainder
from app.evaluation.review.decoys import CopyTopDecoyBuilder, DecoyCardBuilder
from app.evaluation.review.models import ClientCards, ReviewItemDraft, ReviewSampleDesign, SourceCard

Candidate = tuple[ClientCards, SourceCard]


def type_quotas(available: Counter[str], total: int, min_per_type: int) -> dict[str, int]:
    """a floor per type where cards exist, the rest proportional to how often each type is shown."""
    quotas = {kind: min(min_per_type, n) for kind, n in available.items()}
    remaining = total - sum(quotas.values())
    while remaining > 0:
        room = {kind: available[kind] - quotas[kind] for kind in available if available[kind] > quotas[kind]}
        if not room:
            break
        extra = largest_remainder(remaining, {kind: float(available[kind]) for kind in room})
        for kind, n in extra.items():
            take = min(n, room[kind])
            quotas[kind] += take
            remaining -= take
        if all(extra[kind] == 0 for kind in room):
            break
    return {kind: n for kind, n in sorted(quotas.items()) if n}


class ReviewSampleDesigner:
    """12 §5.3: stratified by type, spread over clients, lenses and confidence labels, plus blinded decoys."""

    def __init__(self, settings: ReviewSettings, decoys: DecoyCardBuilder | None = None) -> None:
        self.settings = settings
        self.decoys = decoys or CopyTopDecoyBuilder()

    def design(
        self,
        clients: Sequence[ClientCards],
        seed: int,
        retention_days: int,
        batch_id: uuid.UUID | None = None,
    ) -> ReviewSampleDesign:
        batch = batch_id or uuid.uuid4()
        rng = np.random.default_rng(seed)
        pool: dict[str, list[Candidate]] = defaultdict(list)
        for client in clients:
            for card in client.cards:
                pool[card.type].append((client, card))
        available = Counter({kind: len(items) for kind, items in pool.items()})
        quotas = type_quotas(available, self.settings.sample_size, self.settings.min_per_type)
        chosen: list[Candidate] = []
        per_client: Counter[uuid.UUID] = Counter()
        lenses: Counter[str] = Counter()
        labels: Counter[str] = Counter()
        for kind, quota in quotas.items():
            candidates = [pool[kind][i] for i in rng.permutation(len(pool[kind]))]
            for _ in range(quota):
                best = min(
                    range(len(candidates)),
                    key=lambda i: (
                        per_client[candidates[i][0].client_id],
                        min(lenses[lens] for lens in candidates[i][1].lens),
                        labels[candidates[i][1].confidence_label],
                        i,
                    ),
                )
                client, card = candidates.pop(best)
                chosen.append((client, card))
                per_client[client.client_id] += 1
                lenses.update(card.lens)
                labels[card.confidence_label] += 1
        decoys = self._decoys(clients, rng)
        items = [
            self._item(batch, client, card, is_decoy, rng, retention_days)
            for (client, card), is_decoy in [*((c, False) for c in chosen), *((d, True) for d in decoys)]
        ]
        notes = []
        if len(chosen) < self.settings.sample_size:
            notes.append(
                f"only {len(chosen)} eligible cards; the sample is smaller than {self.settings.sample_size}"
            )
        if len(decoys) < self.settings.decoys:
            notes.append(f"only {len(decoys)} decoys could be built from the tracked channels")
        missing = sorted(kind for kind in quotas if quotas[kind] < self.settings.min_per_type)
        if missing:
            notes.append(f"fewer than {self.settings.min_per_type} cards available for: {', '.join(missing)}")
        return ReviewSampleDesign(
            batch_id=batch,
            items=tuple(items),
            sheets=tuple(client.sheet for client in clients),
            by_type=dict(Counter(card.type for _, card in chosen)),
            notes=tuple(notes),
        )

    def _decoys(self, clients: Sequence[ClientCards], rng: np.random.Generator) -> list[Candidate]:
        order = [clients[i] for i in rng.permutation(len(clients))]
        built = {client.client_id: self.decoys.build(client, self.settings.decoys) for client in order}
        picked: list[Candidate] = []
        depth = 0
        while len(picked) < self.settings.decoys and any(len(cards) > depth for cards in built.values()):
            for client in order:
                cards = built[client.client_id]
                if depth < len(cards) and len(picked) < self.settings.decoys:
                    picked.append((client, cards[depth]))
            depth += 1
        return picked

    @staticmethod
    def _item(
        batch: uuid.UUID,
        client: ClientCards,
        card: SourceCard,
        is_decoy: bool,
        rng: np.random.Generator,
        retention_days: int,
    ) -> ReviewItemDraft:
        return ReviewItemDraft(
            item_id=uuid.uuid4(),
            batch_id=batch,
            workspace_id=client.workspace_id,
            client_id=client.client_id,
            is_decoy=is_decoy,
            source=card,
            display_seed=int(rng.integers(0, 2**31 - 1)),
            expires_at=client.oldest_api_at + timedelta(days=retention_days),
        )
