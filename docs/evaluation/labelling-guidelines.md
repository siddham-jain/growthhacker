# Outlier labelling guidelines

Generated from `app/evaluation/labelling/guidelines.py`; the label pack embeds the same text.

## Question

Compared with this channel's typical video of the same format at a similar age, did this video clearly break out?

Answer one of: `outlier`, `not_outlier`, `unsure`.

## Rules

- Compare like with like: Shorts with Shorts, long-form with long-form, lives with lives.
- Account for age. A 2-day-old video with 5k views can beat a 60-day-old video with 8k.
- Ignore subscriber count and absolute size; judge only against the channel's own norm.
- If the channel has fewer than about 8 comparable videos, answer unsure (reason few_channel_videos).
- If one huge video dominates the channel, judge against the typical video, not that one (viral_skews_channel).
- If the video is under about 3 days old, answer unsure (too_young).
- If the counts look like ad-driven views (very low likes and comments relative to views compared with siblings), mark possible_paid_promotion and still give a label.
- Judge from the numbers in this pack only. Live YouTube counts have moved since the observation time.

## Reason codes

- `format_mismatch`
- `too_young`
- `few_channel_videos`
- `viral_skews_channel`
- `possible_paid_promotion`
- `live_or_premiere`
- `collab`
- `other`

## Calibration

Before labelling, both labellers label the same 10 anchor videos (outside this sample) with known answers and discuss them in a 15-minute call.

## Scoring

- Two labellers label all items; agreement is reported as percent agreement, Cohen's kappa and Gwet's AC1. Kappa below 0.6 means revise these guidelines and relabel a fresh 30.
- Disagreements go to a third labeller (round 2). Items both labellers marked unsure are excluded.
- Precision and recall are weighted by inverse inclusion probability (Horvitz-Thompson).
