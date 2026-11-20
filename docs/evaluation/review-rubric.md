# Human review rubric

Generated from `app/evaluation/review/rubric.py`; the review pack embeds the same text.

Read the client sheet first. Rate every card on the five criteria from 1 to 5 using the anchors, say whether you would ship it, and add a comment if something is wrong. Rate on your own; do not compare notes with the other rater until both packs are exported.

| Criterion | 1 | 3 | 5 |
|---|---|---|---|
| Specific | generic advice ("make engaging videos") | topic named, but format or angle vague | names topic, angle, format/length and title/thumbnail direction |
| Evidenced | references missing, stale or unrelated | references valid but weakly connected to the claim | 3 or more recent, relevant references whose numbers clearly support the claim |
| Relevant to the client | does not fit this business or its buyers | fits the niche, not this client's offerings | ties directly to a tracked buyer question or offering |
| Actionable | cannot brief a creator from it | needs significant extra thinking | could brief a creator today |
| Not obvious | something the client already does or anyone would say | somewhat new | a non-obvious, evidenced gap or angle |

Also collected: "would you ship this card?" (yes/no) and an optional comment.

## Pass rule

- Overall mean (mean over items of the mean over raters and criteria) at least 4.0, and every criterion mean at least 3.5. The lower 95% bootstrap bound and the share of would-ship (target 70%) are reported too.
- Six blinded decoy cards (naive copy-the-most-viewed-video ideas) are mixed in. If real cards beat them by less than 0.3, the ratings are flagged as not discriminating.
- Agreement: Krippendorff's alpha (ordinal) per criterion; below 0.667 triggers a calibration discussion.

## What low scores change (proposals only)

- **Evidenced**: raise min_reference_videos / min_ref_outlier_ratio or the minimum confidence label; require reference topic match score above the acceptance threshold.
- **Specific**: writer schema must fill format, length band and title direction; gate cards whose summary lacks a format or length token.
- **Relevant to the client**: raise 05's topic acceptance threshold for evidence; require buyer_question_id for the conversion lens.
- **Actionable**: require a non-empty spec for format plus a title or thumbnail pattern.
- **Not obvious**: novelty gate: reject when the client's last 90 days hold 2+ matching videos or the spec equals the copy-top spec.
